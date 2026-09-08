import hashlib
import os
import uuid
import io
import glob
import xml.etree.ElementTree as ET
import pandas as pd
import streamlit as st
import psycopg2
from fpdf import FPDF
from datetime import datetime

# Configuração da página Streamlit
st.set_page_config(page_title="Sistema de Almoxarifado", layout="wide", page_icon="📦")

# Pastas para salvamento temporário e relatórios
for pasta in ["uploads", "relatorios_checklist"]:
    if not os.path.exists(pasta):
        os.makedirs(pasta)

# --- MÓDULO DE SEGURANÇA E ARQUIVOS ---
def gerar_hash_senha(senha: str) -> str:
    salt = os.urandom(16)
    hash_bytes = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + hash_bytes.hex()

def verificar_senha(senha_digitada: str, hash_armazenado: str) -> bool:
    try:
        salt_hex, hash_hex = hash_armazenado.split(":")
        salt = bytes.fromhex(salt_hex)
        hash_bytes = bytes.fromhex(hash_hex)
        novo_hash = hashlib.pbkdf2_hmac('sha256', senha_digitada.encode('utf-8'), salt, 100000)
        return novo_hash == hash_bytes
    except Exception:
        return False

EXTENSOES_PERMITIDAS_IMAGEM = {".jpg", ".jpeg", ".png", ".webp"}
EXTENSOES_PERMITIDAS_XML = {".xml"}
EXTENSOES_PERMITIDAS_MAPA = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".xlsx", ".xls"}

def salvar_arquivo_seguro(uploaded_file, pasta_destino="uploads", tipo="imagem") -> str:
    if uploaded_file is None:
        return ""
    _, ext = os.path.splitext(uploaded_file.name)
    ext = ext.lower()
    
    if tipo == "imagem":
        permitidas = EXTENSOES_PERMITIDAS_IMAGEM
    elif tipo == "xml":
        permitidas = EXTENSOES_PERMITIDAS_XML
    elif tipo == "mapa":
        permitidas = EXTENSOES_PERMITIDAS_MAPA
    else:
        permitidas = EXTENSOES_PERMITIDAS_IMAGEM

    if ext not in permitidas:
        raise ValueError(f"Extensão não permitida: {ext}")
        
    novo_nome = f"{uuid.uuid4().hex}{ext}"
    caminho_completo = os.path.join(pasta_destino, novo_nome)
    with open(caminho_completo, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return caminho_completo

def gerar_excel_download(df: pd.DataFrame, nome_arquivo="relatorio.xlsx") -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Relatório')
    return output.getvalue()

# --- LÓGICA DE GERENCIAMENTO DE PDF E LIMPEZA (3 MÁXIMO) ---
def gerenciar_limpeza_pdf_pasta(pasta="relatorios_checklist"):
    """Garante no máximo 3 relatórios salvos. Ao chegar no 4º, apaga os 2 mais antigos."""
    arquivos = glob.glob(os.path.join(pasta, "*.pdf"))
    arquivos.sort(key=os.path.getctime) # Ordena por data de criação (mais antigo primeiro)
    
    if len(arquivos) >= 4:
        # Apaga os 2 mais antigos
        for i in range(2):
            try:
                os.remove(arquivos[i])
            except Exception:
                pass

def gerar_pdf_checklist(titulo_doc, operador, dados_items, observacoes=""):
    gerenciar_limpeza_pdf_pasta() # Executa a limpeza preventiva/rotativa
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(0, 10, "Relatorio de Checklist de Ferramentas", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 7, f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ln=True)
    pdf.cell(0, 7, f"Operador Responsavel: {operador}", ln=True)
    pdf.ln(5)
    
    # Tabela
    pdf.set_font("Helvetica", 'B', 10)
    pdf.cell(80, 8, "Ferramenta / Item", border=1)
    pdf.cell(40, 8, "Status", border=1)
    pdf.cell(70, 8, "Obs", border=1, ln=True)
    
    pdf.set_font("Helvetica", size=9)
    for item in dados_items:
        pdf.cell(80, 7, str(item['ferramenta'])[:35], border=1)
        pdf.cell(40, 7, str(item['status']), border=1)
        pdf.cell(70, 7, str(item['obs'])[:30], border=1, ln=True)
        
    if observacoes:
        pdf.ln(5)
        pdf.set_font("Helvetica", 'B', 10)
        pdf.cell(0, 7, "Observacoes Gerais:", ln=True)
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(0, 5, observacoes)
        
    nome_arquivo = f"checklist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    caminho_pdf = os.path.join("relatorios_checklist", nome_arquivo)
    pdf.output(caminho_pdf)
    return caminho_pdf

# --- CONEXÃO POOLED COM SUPABASE ---
SUPABASE_DB_URL = st.secrets.get(
    "SUPABASE_DB_URL",
    "postgresql://postgres:SUA_SENHA@db.SEU_PROJETO.supabase.co:6543/postgres"
)

@st.cache_resource
def obter_conexao():
    return psycopg2.connect(SUPABASE_DB_URL)

def conectar():
    try:
        conn = obter_conexao()
        if conn.closed != 0:
            st.cache_resource.clear()
            return obter_conexao()
        if conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
            conn.rollback()
        return conn
    except Exception:
        st.cache_resource.clear()
        return obter_conexao()

def inicializar_banco():
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes (
                id INTEGER PRIMARY KEY DEFAULT 1,
                nome_empresa TEXT DEFAULT 'Sistema de Almoxarifado',
                logo_path TEXT DEFAULT '',
                cor_tema TEXT DEFAULT '#2196F3',
                mapa_path TEXT DEFAULT ''
            );
            """)
            cursor.execute("INSERT INTO configuracoes (id, nome_empresa) VALUES (1, 'Sistema de Almoxarifado') ON CONFLICT (id) DO NOTHING;")
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                categoria TEXT DEFAULT 'Geral',
                localizacao TEXT DEFAULT 'Não informada',
                quantidade REAL NOT NULL DEFAULT 0,
                unidade_medida TEXT DEFAULT 'Caixa',
                qtd_por_caixa INTEGER DEFAULT 1,
                foto_path TEXT DEFAULT '',
                codigo_barras TEXT DEFAULT ''
            );
            """)
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                produto_id INTEGER REFERENCES produtos (id) ON DELETE CASCADE,
                tipo TEXT NOT NULL,
                quantidade REAL NOT NULL,
                usuario TEXT DEFAULT 'Sistema',
                responsavel_epi TEXT DEFAULT '',
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                usuario TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL,
                ultima_atividade TIMESTAMP,
                localizacao_sessao TEXT DEFAULT 'Almoxarifado Principal'
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS notas_fiscais (
                id SERIAL PRIMARY KEY,
                numero_nf TEXT NOT NULL,
                fornecedor TEXT NOT NULL,
                cnpj_fornecedor TEXT DEFAULT '',
                produto_nome TEXT NOT NULL,
                quantidade REAL NOT NULL,
                valor_unitario REAL DEFAULT 0.0,
                valor_total REAL DEFAULT 0.0,
                data_recebimento TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usuario TEXT DEFAULT 'Sistema'
            );
            """)
            
            # Tabela de Checklists
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS checklist_ferramentas (
                id SERIAL PRIMARY KEY,
                usuario TEXT NOT NULL,
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pdf_path TEXT DEFAULT '',
                observacao TEXT DEFAULT ''
            );
            """)

            cursor.execute("SELECT COUNT(*) FROM usuarios")
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("admin", gerar_hash_senha("1234"), "Admin"))
                cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("operador", gerar_hash_senha("1234"), "Operador"))
            conn.commit()
    except Exception as e:
        conn.rollback()
        st.cache_resource.clear()
        raise e

if 'banco_inicializado' not in st.session_state:
    inicializar_banco()
    st.session_state.banco_inicializado = True

# --- CONSULTAS BANCO DE DADOS ---
def atualizar_presenca_usuario(usuario, localizacao="Almoxarifado Principal"):
    try:
        conn = conectar()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE usuarios SET ultima_atividade = NOW(), localizacao_sessao = %s WHERE usuario = %s", (localizacao, usuario))
        conn.commit()
    except Exception:
        pass

@st.cache_data(ttl=300)
def buscar_configuracoes():
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("SELECT nome_empresa, logo_path, cor_tema, mapa_path FROM configuracoes WHERE id = 1")
        res = cursor.fetchone()
        return {
            "nome_empresa": res[0] if res else "Sistema de Almoxarifado",
            "logo_path": res[1] if res else "",
            "cor_tema": res[2] if res else "#2196F3",
            "mapa_path": res[3] if res else ""
        }

def salvar_configuracoes(nome, logo_path, cor, mapa_path=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE configuracoes SET nome_empresa = %s, logo_path = %s, cor_tema = %s, mapa_path = %s WHERE id = 1", (nome, logo_path, cor, mapa_path))
    conn.commit()
    st.cache_data.clear()

def autenticar_usuario(usuario, senha_digitada):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("SELECT perfil, senha FROM usuarios WHERE usuario = %s", (usuario,))
        res = cursor.fetchone()
        if res:
            perfil, hash_senha = res
            if ":" not in hash_senha:
                if senha_digitada == hash_senha:
                    novo_hash = gerar_hash_senha(senha_digitada)
                    cursor.execute("UPDATE usuarios SET senha = %s WHERE usuario = %s", (novo_hash, usuario))
                    conn.commit()
                    return perfil
            elif verificar_senha(senha_digitada, hash_senha):
                return perfil
    return None

@st.cache_data(ttl=15)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras FROM produtos ORDER BY id ASC", conn)

def cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida="Caixa", qtd_por_caixa=1, foto_path="", codigo_barras=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO produtos (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras)
        )
    conn.commit()
    st.cache_data.clear()

def editar_produto(prod_id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path="", codigo_barras=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE produtos SET nome = %s, categoria = %s, localizacao = %s, quantidade = %s, unidade_medida = %s, qtd_por_caixa = %s, foto_path = %s, codigo_barras = %s WHERE id = %s",
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, prod_id)
        )
    conn.commit()
    st.cache_data.clear()

def excluir_produto(prod_id):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM produtos WHERE id = %s", (prod_id,))
    conn.commit()
    st.cache_data.clear()

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado, responsavel_epi=""):
    if tipo == "SAÍDA" and qtd_mov > qtd_atual:
        return False, f"Estoque insuficiente! Saldo atual: {qtd_atual:.2f}"
    nova_qtd = qtd_atual + qtd_mov if tipo == "ENTRADA" else qtd_atual - qtd_mov
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE produtos SET quantidade = %s WHERE id = %s", (nova_qtd, prod_id))
        cursor.execute(
            "INSERT INTO historico (produto_id, tipo, quantidade, usuario, responsavel_epi) VALUES (%s, %s, %s, %s, %s)",
            (prod_id, tipo, qtd_mov, usuario_logado, responsavel_epi)
        )
    conn.commit()
    st.cache_data.clear()
    return True, "Movimentação realizada com sucesso!"

def dar_entrada_nota_fiscal(numero_nf, fornecedor, cnpj, nome_prod, qtd_mov, valor_unit, usuario_logado, categoria="Geral", localizacao="Almoxarifado Principal"):
    valor_total = qtd_mov * valor_unit
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, quantidade FROM produtos WHERE LOWER(nome) = LOWER(%s)", (nome_prod.strip(),))
        res_prod = cursor.fetchone()
        if res_prod:
            prod_id, qtd_atual = res_prod
            nova_qtd = qtd_atual + qtd_mov
            cursor.execute("UPDATE produtos SET quantidade = %s WHERE id = %s", (nova_qtd, prod_id))
        else:
            cursor.execute("INSERT INTO produtos (nome, categoria, localizacao, quantidade) VALUES (%s, %s, %s, %s) RETURNING id", (nome_prod.strip(), categoria, localizacao, qtd_mov))
            prod_id = cursor.fetchone()[0]
        
        cursor.execute("""
        INSERT INTO notas_fiscais (numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, valor_unitario, valor_total, usuario)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (numero_nf, fornecedor, cnpj, nome_prod, qtd_mov, valor_unit, valor_total, usuario_logado))
        
        cursor.execute(
            "INSERT INTO historico (produto_id, tipo, quantidade, usuario) VALUES (%s, %s, %s, %s)",
            (prod_id, f"ENTRADA (NF {numero_nf})", qtd_mov, usuario_logado)
        )
    conn.commit()
    st.cache_data.clear()
    return True

def processar_xml_nfe(xml_file):
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'} if 'portalfiscal' in root.tag else {}
        def get_tag(element, path):
            node = element.find(path, ns) if ns else element.find(path)
            return node.text if node is not None else ""
        
        ide = root.find('.//nfe:ide', ns) if ns else root.find('.//ide')
        emit = root.find('.//nfe:emit', ns) if ns else root.find('.//emit')
        numero_nf = get_tag(ide, 'nfe:nNF') if ns else get_tag(ide, 'nNF')
        fornecedor = get_tag(emit, 'nfe:xNome') if ns else get_tag(emit, 'xNome')
        cnpj = get_tag(emit, 'nfe:CNPJ') if ns else get_tag(emit, 'CNPJ')
        
        itens = []
        det_list = root.findall('.//nfe:det', ns) if ns else root.findall('.//det')
        for det in det_list:
            prod = det.find('nfe:prod', ns) if ns else det.find('prod')
            nome_item = get_tag(prod, 'nfe:xProd') if ns else get_tag(prod, 'xProd')
            raw_qtd = get_tag(prod, 'nfe:qCom') if ns else get_tag(prod, 'qCom')
            raw_unit = get_tag(prod, 'nfe:vUnCom') if ns else get_tag(prod, 'vUnCom')
            qtd_item = float(raw_qtd) if raw_qtd else 0.0
            val_unit = float(raw_unit) if raw_unit else 0.0
            itens.append({"produto": nome_item, "quantidade": qtd_item, "valor_unitario": val_unit, "valor_total": qtd_item * val_unit})
        return True, {"numero_nf": numero_nf, "fornecedor": fornecedor, "cnpj": cnpj, "itens": itens}
    except Exception as e:
        return False, f"Erro ao ler arquivo XML: {str(e)}"

@st.cache_data(ttl=60)
def buscar_notas_fiscais():
    conn = conectar()
    return pd.read_sql_query("SELECT id, numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, valor_unitario, valor_total, data_recebimento, usuario FROM notas_fiscais ORDER BY id DESC", conn)

@st.cache_data(ttl=15)
def buscar_usuarios():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT id, usuario, perfil, ultima_atividade, localizacao_sessao,
    CASE WHEN ultima_atividade >= NOW() - INTERVAL '5 minutes' THEN '🟢 Online' ELSE '🔴 Offline' END as status_online
    FROM usuarios ORDER BY id ASC
    """, conn)

def cadastrar_usuario(usuario, senha, perfil):
    try:
        hash_s = gerar_hash_senha(senha)
        conn = conectar()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", (usuario, hash_s, perfil))
        conn.commit()
        st.cache_data.clear()
        return True, f"Usuário '{usuario}' cadastrado com sucesso!"
    except Exception as e:
        return False, f"Erro ao cadastrar usuário: {e}"

def salvar_registro_checklist(usuario, pdf_path, observacao=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO checklist_ferramentas (usuario, pdf_path, observacao) VALUES (%s, %s, %s)", (usuario, pdf_path, observacao))
    conn.commit()

@st.cache_data(ttl=15)
def buscar_historico():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT h.id, p.nome as produto, p.categoria, h.tipo, h.quantidade, h.usuario, h.responsavel_epi, h.data_hora
    FROM historico h LEFT JOIN produtos p ON h.produto_id = p.id ORDER BY h.id DESC
    """, conn)

# --- INTERFACE E NAVEGAÇÃO ---
config = buscar_configuracoes()
st.markdown(f"""
<style>
.stButton>button[kind="primary"] {{
    background-color: {config['cor_tema']};
    border-color: {config['cor_tema']};
}}
.metric-card {{
    background-color: #f8f9fa;
    border-radius: 8px;
    padding: 15px;
    border-left: 5px solid {config['cor_tema']};
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}}
</style>
""", unsafe_allow_html=True)

if 'logado' not in st.session_state:
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""

if not st.session_state.logado:
    st.title(f"📦 {config['nome_empresa']}")
    if config['logo_path'] and os.path.exists(config['logo_path']):
        st.image(config['logo_path'], width=180)
    col1, _ = st.columns([1, 2])
    with col1:
        user_input = st.text_input("Usuário")
        pass_input = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            perfil = autenticar_usuario(user_input, pass_input)
            if perfil:
                st.session_state.logado = True
                st.session_state.usuario = user_input
                st.session_state.perfil = perfil
                atualizar_presenca_usuario(user_input)
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos!")
    st.stop()

# Heartbeat do Usuário Logado
atualizar_presenca_usuario(st.session_state.usuario)

if config['logo_path'] and os.path.exists(config['logo_path']):
    st.sidebar.image(config['logo_path'], use_container_width=True)
st.sidebar.title(config['nome_empresa'])
st.sidebar.write(f"👤 **{st.session_state.usuario}** ({st.session_state.perfil})")

if config['mapa_path'] and os.path.exists(config['mapa_path']):
    st.sidebar.write("---")
    st.sidebar.subheader("🗺️ Layout / Mapa")
    ext = os.path.splitext(config['mapa_path'])[1].lower()
    with open(config['mapa_path'], "rb") as f:
        bytes_file = f.read()
    st.sidebar.download_button(
        label="📥 Baixar Mapa do Almoxarifado",
        data=bytes_file,
        file_name=f"mapa_almoxarifado{ext}",
        mime="application/pdf" if ext == ".pdf" else "application/octet-stream"
    )

opcoes_menu = [
    "📋 Consulta de Estoque",
    "🛠️ Checklist de Ferramentas",
    "📦 Retirada de Materiais",
    "➕ Cadastrar Produto",
    "📄 Entrada de NF (XML Auto)",
    "📥 Importar Dados (Excel / Sheets)",
    "📊 Dashboard Analytics (BI)",
    "📜 Histórico / Auditoria"
]

if st.session_state.perfil == "Admin":
    opcoes_menu.append("👥 Gerenciar Usuários")
    opcoes_menu.append("⚙️ Personalizar Empresa")

opcao = st.sidebar.radio("Navegação", opcoes_menu)

if st.sidebar.button("🚪 Sair / Logout"):
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""
    st.rerun()

# --- ABA 1: CONSULTA DE ESTOQUE ---
if opcao == "📋 Consulta de Estoque":
    st.title(f"📦 Controle de Estoque - {config['nome_empresa']}")
    df_prod = buscar_produtos()
    bar_search = st.text_input("🔍 Bipar Código de Barras (Leitor USB/Bluetooth):", key="bar_search")
    
    if not df_prod.empty:
        df_prod['Status'] = df_prod['quantidade'].apply(lambda x: "⚠️ REPOR" if x < 5 else "✅ OK")
        def formatar_saldo(row):
            medida = row['unidade_medida']
            if medida == 'Caixa':
                qtd_cx = row['quantidade'] / row['qtd_por_caixa'] if row['qtd_por_caixa'] > 0 else 0
                return f"{row['quantidade']:.0f} un ({qtd_cx:.2f} CX)"
            elif medida == 'Metro':
                return f"{row['quantidade']:.2f} Mts"
            elif medida == 'Pacote':
                return f"{row['quantidade']:.0f} Pacotes"
            else:
                return f"{row['quantidade']:.0f} Unidades"
        df_prod['Saldo Formatado'] = df_prod.apply(formatar_saldo, axis=1)
    else:
        df_prod['Status'] = pd.Series(dtype='str')
        df_prod['Saldo Formatado'] = pd.Series(dtype='str')

    c_busca1, c_busca2 = st.columns([2, 1])
    with c_busca1:
        busca = st.text_input("🔎 Buscar por nome ou categoria:")
    with c_busca2:
        busca_loc = st.text_input("📍 Filtrar por Localização / Setor:")

    if not df_prod.empty:
        if bar_search.strip():
            df_prod = df_prod[df_prod['codigo_barras'].astype(str) == bar_search.strip()]
        else:
            if busca:
                df_prod = df_prod[df_prod['nome'].str.contains(busca, case=False, na=False) | df_prod['categoria'].str.contains(busca, case=False, na=False)]
            if busca_loc:
                df_prod = df_prod[df_prod['localizacao'].str.contains(busca_loc, case=False, na=False)]

    col_tbl, col_exp = st.columns([4, 1])
    with col_tbl:
        st.dataframe(df_prod[['id', 'codigo_barras', 'nome', 'categoria', 'localizacao', 'unidade_medida', 'Saldo Formatado', 'Status']], use_container_width=True)
    with col_exp:
        if not df_prod.empty:
            st.download_button(
                label="📥 Exportar Excel",
                data=gerar_excel_download(df_prod),
                file_name="estoque_atual.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# --- ABA 2: CHECKLIST DE FERRAMENTAS ---
elif opcao == "🛠️ Checklist de Ferramentas":
    st.title("🛠️ Checklist de Ferramentas e Equipamentos")
    st.caption("Realize a conferência das ferramentas. Ao salvar, um PDF será gerado automaticamente. O sistema mantém os 3 PDFs mais recentes.")

    df_prod = buscar_produtos()
    # Filtra produtos categorizados como Ferramentas ou Equipamentos
    df_ferramentas = df_prod[df_prod['categoria'].str.contains("Ferramenta|Equipamento|EPI", case=False, na=False)] if not df_prod.empty else pd.DataFrame()

    if df_ferramentas.empty:
        st.warning("Nenhuma ferramenta cadastrada. Cadastre produtos com categoria 'Ferramentas' para utilizar esta aba.")
    else:
        st.subheader("📋 Lista de Verificação")
        
        with st.form("form_checklist"):
            itens_checklist = []
            for idx, row in df_ferramentas.iterrows():
                st.markdown(f"**Item:** `{row['nome']}` | Local: *{row['localizacao']}*")
                c1, c2 = st.columns([2, 3])
                with c1:
                    status = st.selectbox(f"Status - {row['nome']}", ["OK / Operacional", "Defeituoso", "Em Manutenção", "Ausente"], key=f"status_{row['id']}")
                with c2:
                    obs_item = st.text_input(f"Observações - {row['nome']}", key=f"obs_{row['id']}", placeholder="Detalhes de danos/avarias...")
                itens_checklist.append({"ferramenta": row['nome'], "status": status, "obs": obs_item})
                st.write("---")

            obs_gerais = st.text_area("Observações Gerais da Inspeção:", placeholder="Informe detalhes do estado do carrinho, maletas, etc.")
            
            btn_gerar_chk = st.form_submit_button("💾 Finalizar Checklist e Gerar PDF", type="primary")

        if btn_gerar_chk:
            try:
                # Gera o PDF (que automaticamente remove relatórios antigos se for o 4º)
                path_pdf = gerar_pdf_checklist("Checklist Diário", st.session_state.usuario, itens_checklist, obs_gerais)
                salvar_registro_checklist(st.session_state.usuario, path_pdf, obs_gerais)
                st.success("✅ Checklist concluído com sucesso! Relatório PDF gerado.")
                
                with open(path_pdf, "rb") as f:
                    st.download_button(
                        label="📄 Baixar Relatório PDF Gerado",
                        data=f.read(),
                        file_name=os.path.basename(path_pdf),
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Erro ao gerar relatório: {e}")

    st.write("---")
    st.subheader("📂 Relatórios Salvos no Servidor (Máximo 3 Ativos)")
    pdfs_salvos = glob.glob(os.path.join("relatorios_checklist", "*.pdf"))
    pdfs_salvos.sort(key=os.path.getctime, reverse=True)

    if pdfs_salvos:
        for pdf_path in pdfs_salvos:
            nome_arq = os.path.basename(pdf_path)
            data_criacao = datetime.fromtimestamp(os.path.getctime(pdf_path)).strftime('%d/%m/%Y %H:%M:%S')
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"📄 **{nome_arq}** (Criado em: {data_criacao})")
            with col2:
                with open(pdf_path, "rb") as f:
                    st.download_button(label="📥 Baixar", data=f.read(), file_name=nome_arq, mime="application/pdf", key=nome_arq)
    else:
        st.info("Nenhum relatório PDF disponível no momento.")

# --- ABA 3: RETIRADA DE MATERIAIS ---
elif opcao == "📦 Retirada de Materiais":
    st.title("📦 Retirada e Saída de Materiais do Almoxarifado")
    df_prod = buscar_produtos()
    
    if df_prod.empty:
        st.info("Nenhum produto cadastrado para retirada.")
    else:
        st.subheader("1️⃣ Selecionar Produto")
        cod_bipado = st.text_input("🔍 Bipar Código de Barras para Selecionar Rápidamente:")
        
        prod_selecionado = None
        if cod_bipado.strip():
            match = df_prod[df_prod['codigo_barras'].astype(str) == cod_bipado.strip()]
            if not match.empty:
                prod_selecionado = match.iloc[0]['nome']
                st.success(f"Item Encontrado: **{prod_selecionado}**")
            else:
                st.warning("Código de barras não encontrado no cadastro.")
                
        if not prod_selecionado:
            prod_selecionado = st.selectbox("Ou Escolha o Item na Lista:", df_prod['nome'].tolist())

        row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
        
        st.write("---")
        st.subheader("2️⃣ Dados da Retirada")
        col1, col2 = st.columns(2)
        with col1:
            st.info(f"**Item:** {row['nome']} \n\n**Categoria:** {row['categoria']} \n\n**Estoque Atual:** {row['quantidade']} ({row['unidade_medida']})")
            if row['unidade_medida'] == 'Caixa':
                qtd_mov_final = st.number_input(f"Qtd a Retirar (por Caixas com {row['qtd_por_caixa']} itens):", min_value=0.1, step=0.5, value=1.0) * row['qtd_por_caixa']
            else:
                qtd_mov_final = st.number_input(f"Qtd a Retirar ({row['unidade_medida']}):", min_value=0.1, step=1.0, value=1.0)

        eh_epi = "EPI" in str(row['categoria']).upper() or "EPI" in str(row['nome']).upper()
        responsavel_epi = ""
        
        with col2:
            if eh_epi:
                st.warning("⚠️ **ESTE ITEM É UM EPI!**")
                responsavel_epi = st.text_input("👤 Nome/ Matrícula do Colaborador (OBRIGATÓRIO PARA EPI):")
            else:
                st.write("Retirada padrão de almoxarifado.")

        st.write("---")
        if st.button("Confirmar Retirada de Item", type="primary"):
            if eh_epi and not responsavel_epi.strip():
                st.error("Erro: Preencha o nome/matrícula da pessoa que está retirando o EPI!")
            else:
                ok, msg = movimentar_produto(int(row['id']), "SAÍDA", float(qtd_mov_final), float(row['quantidade']), st.session_state.usuario, responsavel_epi)
                if ok:
                    st.success(f"Retirada concluída! {msg}")
                    st.rerun()
                else:
                    st.error(msg)

# --- ABA 4: CADASTRO DE PRODUTO ---
elif opcao == "➕ Cadastrar Produto":
    st.title("➕ Cadastrar Novo Produto")
    with st.form("form_cad_prod"):
        c1, c2 = st.columns(2)
        with c1:
            nome = st.text_input("Nome do Produto *")
            codigo_barras = st.text_input("🏷️ Código de Barras (Bipar ou Digitar)")
            categoria = st.text_input("Categoria (Ex: Ferramentas, EPI, Elétrica)", value="Geral")
            localizacao = st.text_input("Localização / Corredor / Prateleira", value="Almoxarifado Principal")
        with c2:
            unidade_medida = st.selectbox("Unidade de Medida", ["Caixa", "Metro", "Pacote", "Unidade"])
            qtd_por_caixa = 1
            if unidade_medida == "Caixa":
                qtd_por_caixa = st.number_input("Qtd de Itens dentro da Caixa", min_value=1, value=1)
            quantidade = st.number_input("Quantidade Inicial em Estoque", min_value=0.0, step=1.0, value=0.0)
            foto = st.file_uploader("Foto do Produto (Opcional)", type=["jpg", "png", "jpeg"])

        sub = st.form_submit_button("💾 Cadastrar Produto")
        if sub:
            if nome.strip():
                foto_path = ""
                if foto is not None:
                    foto_path = salvar_arquivo_seguro(foto, tipo="imagem")
                cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras)
                st.success(f"Produto '{nome}' cadastrado com sucesso!")
                st.rerun()
            else:
                st.warning("O nome do produto é obrigatório!")

# --- ABA 5: ENTRADA POR NOTA FISCAL ---
elif opcao == "📄 Entrada de NF (XML Auto)":
    st.title("📄 Recebimento e Entrada por Nota Fiscal")
    aba_xml, aba_manual = st.tabs(["📥 Importar Arquivo XML (Automático)", "✍️ Lançamento Manual"])
    
    with aba_xml:
        uploaded_xml = st.file_uploader("Arraste ou selecione o arquivo .xml da Nota Fiscal:", type=["xml"])
        if uploaded_xml is not None:
            sucesso, dados_nfe = processar_xml_nfe(uploaded_xml)
            if sucesso:
                st.success(f"XML lido com sucesso! Nota Fiscal Nº **{dados_nfe['numero_nf']}**")
                st.write(f"**Fornecedor:** {dados_nfe['fornecedor']} | **CNPJ:** {dados_nfe['cnpj']}")
                df_itens = pd.DataFrame(dados_nfe['itens'])
                st.dataframe(df_itens, use_container_width=True)
                
                if st.button("Confirmar e Dar Entrada Automática no Estoque", type="primary"):
                    count_sucesso = 0
                    for item in dados_nfe['itens']:
                        dar_entrada_nota_fiscal(dados_nfe['numero_nf'], dados_nfe['fornecedor'], dados_nfe['cnpj'], item['produto'], float(item['quantidade']), float(item['valor_unitario']), st.session_state.usuario)
                        count_sucesso += 1
                    st.success(f"Entrada concluída! {count_sucesso} itens atualizados/cadastrados.")
                    st.rerun()
            else:
                st.error(dados_nfe)

    with aba_manual:
        with st.form("form_nf_manual"):
            c1, c2 = st.columns(2)
            with c1:
                num_nf = st.text_input("Número da Nota Fiscal")
                fornecedor = st.text_input("Fornecedor / Empresa")
                cnpj = st.text_input("CNPJ (Opcional)")
                nome_prod = st.text_input("Nome do Produto")
            with c2:
                qtd_nf = st.number_input("Quantidade Recebida", min_value=0.1, step=1.0)
                val_unit = st.number_input("Valor Unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
                st.write(f"**Valor Total Estimado:** R$ {qtd_nf * val_unit:.2f}")

            if st.form_submit_button("Salvar e Dar Entrada"):
                if num_nf and fornecedor and nome_prod:
                    dar_entrada_nota_fiscal(num_nf, fornecedor, cnpj, nome_prod, float(qtd_nf), float(val_unit), st.session_state.usuario)
                    st.success("Nota Fiscal lançada e estoque atualizado com sucesso!")
                    st.rerun()

    st.write("---")
    st.subheader("📋 Registros de Notas Fiscais Lançadas")
    df_nf = buscar_notas_fiscais()
    st.dataframe(df_nf, use_container_width=True)

# --- ABA 6: IMPORTAR DADOS ---
elif opcao == "📥 Importar Dados (Excel / Sheets)":
    st.title("📥 Importação em Lote de Produtos")
    tab_excel, tab_sheets = st.tabs(["📊 Importar de Planilha Excel (.xlsx)", "🌐 Importar de Google Sheets"])
    
    with tab_excel:
        file_excel = st.file_uploader("Selecione o arquivo .xlsx:", type=["xlsx", "xls"])
        if file_excel is not None:
            try:
                df_imp = pd.read_excel(file_excel)
                st.dataframe(df_imp, use_container_width=True)
                if st.button("Confirmar e Importar para o Banco", type="primary"):
                    qtd_importados = 0
                    for idx, row in df_imp.iterrows():
                        cadastrar_produto(str(row['nome']), str(row.get('categoria', 'Geral')), str(row.get('localizacao', 'Não informada')), float(row.get('quantidade', 0)), str(row.get('unidade_medida', 'Caixa')), int(row.get('qtd_por_caixa', 1)), codigo_barras=str(row.get('codigo_barras', '')))
                        qtd_importados += 1
                    st.success(f"{qtd_importados} produtos cadastrados com sucesso!")
                    st.rerun()
            except Exception as e:
                st.error(f"Erro ao ler arquivo Excel: {e}")

    with tab_sheets:
        sheet_id = st.text_input("ID da Planilha do Google Sheets:")
        if sheet_id:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
            try:
                df_gsheets = pd.read_csv(url)
                st.dataframe(df_gsheets, use_container_width=True)
                if st.button("Importar Dados do Google Sheets", type="primary"):
                    qtd_importados = 0
                    for idx, row in df_gsheets.iterrows():
                        cadastrar_produto(str(row['nome']), str(row.get('categoria', 'Geral')), str(row.get('localizacao', 'Não informada')), float(row.get('quantidade', 0)), str(row.get('unidade_medida', 'Caixa')), int(row.get('qtd_por_caixa', 1)), codigo_barras=str(row.get('codigo_barras', '')))
                        qtd_importados += 1
                    st.success(f"{qtd_importados} produtos importados!")
                    st.rerun()
            except Exception as e:
                st.error(f"Erro ao acessar planilha: {e}")

# --- ABA 7: DASHBOARD BI ---
elif opcao == "📊 Dashboard Analytics (BI)":
    st.title("📊 BI Dashboard - Indicadores do Almoxarifado")
    df_prod = buscar_produtos()
    df_hist = buscar_historico()

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.metric("Total de Produtos", len(df_prod))
    with kpi2:
        itens_criticos = len(df_prod[df_prod['quantidade'] < 5]) if not df_prod.empty else 0
        st.metric("Itens com Estoque Baixo", itens_criticos, delta_color="inverse")
    with kpi3:
        total_retiradas = len(df_hist[df_hist['tipo'] == 'SAÍDA']) if not df_hist.empty else 0
        st.metric("Total de Retiradas", total_retiradas)
    with kpi4:
        retiradas_epi = len(df_hist[df_hist['responsavel_epi'] != '']) if not df_hist.empty else 0
        st.metric("Retiradas de EPI", retiradas_epi)

    st.write("---")
    col_ranking, col_cat = st.columns([3, 2])
    with col_ranking:
        st.subheader("Top 10 Itens Mais Retirados")
        if not df_hist.empty:
            df_saidas = df_hist[df_hist['tipo'] == 'SAÍDA']
            if not df_saidas.empty:
                top10 = df_saidas.groupby('produto')['quantidade'].sum().reset_index()
                top10 = top10.sort_values(by='quantidade', ascending=False).head(10)
                st.bar_chart(top10.set_index('produto'))
            else:
                st.info("Nenhuma saída registrada até o momento.")
        else:
            st.info("Sem dados de histórico.")

    with col_cat:
        st.subheader("Distribuição por Categoria")
        if not df_prod.empty:
            cat_count = df_prod['categoria'].value_counts()
            st.bar_chart(cat_count)

# --- ABA 8: HISTÓRICO ---
elif opcao == "📜 Histórico / Auditoria":
    st.title("📜 Histórico e Auditoria de Movimentações")
    df_hist = buscar_historico()
    c1, c2 = st.columns([4, 1])
    with c1:
        st.dataframe(df_hist, use_container_width=True)
    with c2:
        if not df_hist.empty:
            st.download_button(
                label="📥 Exportar Excel",
                data=gerar_excel_download(df_hist),
                file_name="historico_movimentacoes.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# --- ABA 9: GERENCIAR USUÁRIOS (ADMIN) ---
elif opcao == "👥 Gerenciar Usuários" and st.session_state.perfil == "Admin":
    st.title("👥 Gerenciamento de Usuários e Monitoramento")
    df_usr = buscar_usuarios()
    st.dataframe(df_usr[['id', 'usuario', 'perfil', 'status_online', 'ultima_atividade', 'localizacao_sessao']], use_container_width=True)
    
    st.write("---")
    st.subheader("➕ Cadastrar Novo Usuário")
    with st.form("form_cad_usr"):
        u_nome = st.text_input("Nome do Usuário")
        u_senha = st.text_input("Senha", type="password")
        u_perfil = st.selectbox("Perfil de Acesso", ["Operador", "Admin"])
        if st.form_submit_button("Cadastrar Usuário"):
            ok, msg = cadastrar_usuario(u_nome, u_senha, u_perfil)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

# --- ABA 10: PERSONALIZAR EMPRESA (ADMIN) ---
elif opcao == "⚙️ Personalizar Empresa" and st.session_state.perfil == "Admin":
    st.title("⚙️ Configurações da Empresa e Layout")
    with st.form("form_cfg"):
        e_nome = st.text_input("Nome da Empresa", value=config['nome_empresa'])
        e_cor = st.color_picker("Cor do Tema", value=config['cor_tema'])
        e_logo = st.file_uploader("Atualizar Logomarca (Imagem)", type=["png", "jpg", "jpeg"])
        e_mapa = st.file_uploader("Atualizar Mapa do Almoxarifado (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])
        
        if st.form_submit_button("💾 Salvar Configurações"):
            logo_p = config['logo_path']
            mapa_p = config['mapa_path']
            if e_logo is not None:
                logo_p = salvar_arquivo_seguro(e_logo, tipo="imagem")
            if e_mapa is not None:
                mapa_p = salvar_arquivo_seguro(e_mapa, tipo="mapa")
            salvar_configuracoes(e_nome, logo_p, e_cor, mapa_p)
            st.success("Configurações atualizadas!")
            st.rerun()
