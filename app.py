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

# --- LÓGICA DE GERENCIAMENTO DE PDF E LIMPEZA ---
def gerenciar_limpeza_pdf_pasta(pasta="relatorios_checklist"):
    """Garante no máximo 3 relatórios salvos. Ao chegar no 4º, apaga os 2 mais antigos."""
    arquivos = glob.glob(os.path.join(pasta, "*.pdf"))
    arquivos.sort(key=os.path.getctime)
    
    if len(arquivos) >= 4:
        for i in range(2):
            try:
                os.remove(arquivos[i])
            except Exception:
                pass

def gerar_pdf_checklist(titulo_doc, operador, dados_items, observacoes=""):
    gerenciar_limpeza_pdf_pasta()
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(0, 10, "Relatorio de Checklist de Ferramentas", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 7, f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ln=True)
    pdf.cell(0, 7, f"Operador Responsavel: {operador}", ln=True)
    pdf.ln(5)
    
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
            cursor.execute("INSERT INTO produtos (nome, categoria, localizacao, quantidade, unidade_medida) VALUES (%s, %s, %s, %s, 'Unidade') RETURNING id", (nome_prod.strip(), categoria, localizacao, qtd_mov))
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
            itens.append({
                "Importar": True,
                "produto": nome_item,
                "quantidade": qtd_item,
                "valor_unitario": val_unit,
                "valor_total": qtd_item * val_unit
            })
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

# Heartbeat
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

# LISTA COMPLETA DOS MENUS
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
    st.caption("Realize a conferência das ferramentas. Os EPIs e insumos de consumo não aparecem nesta tela.")

    df_prod = buscar_produtos()
    
    if not df_prod.empty:
        filtro_incluir = df_prod['categoria'].str.contains("Ferramenta|Equipamento", case=False, na=False)
        filtro_excluir_epi = ~df_prod['categoria'].str.contains("EPI", case=False, na=False) & ~df_prod['nome'].str.contains("EPI", case=False, na=False)
        df_ferramentas = df_prod[filtro_incluir & filtro_excluir_epi]
    else:
        df_ferramentas = pd.DataFrame()

    if df_ferramentas.empty:
        st.warning("Nenhuma ferramenta/equipamento cadastrado.")
    else:
        with st.form("form_checklist"):
            itens_checklist = []
            for idx, row in df_ferramentas.iterrows():
                st.markdown(f"**Item:** `{row['nome']}` | Local: *{row['localizacao']}*")
                c1, c2 = st.columns([2, 3])
                with c1:
                    status = st.selectbox(f"Status - {row['nome']}", ["OK / Operacional", "Defeituoso", "Em Manutenção", "Ausente"], key=f"status_{row['id']}")
                with c2:
                    obs_item = st.text_input(f"Observações - {row['nome']}", key=f"obs_{row['id']}")
                itens_checklist.append({"ferramenta": row['nome'], "status": status, "obs": obs_item})
                st.write("---")

            obs_gerais = st.text_area("Observações Gerais da Inspeção:")
            btn_gerar_chk = st.form_submit_button("💾 Finalizar Checklist e Gerar PDF", type="primary")

        if btn_gerar_chk:
            path_pdf = gerar_pdf_checklist("Checklist Diário", st.session_state.usuario, itens_checklist, obs_gerais)
            salvar_registro_checklist(st.session_state.usuario, path_pdf, obs_gerais)
            st.success("✅ Checklist concluído com sucesso!")

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

# --- ABA 3: RETIRADA DE MATERIAIS ---
elif opcao == "📦 Retirada de Materiais":
    st.title("📦 Retirada e Saída de Materiais do Almoxarifado")
    df_prod = buscar_produtos()
    
    if df_prod.empty:
        st.info("Nenhum produto cadastrado para retirada.")
    else:
        prod_selecionado = st.selectbox("Escolha o Item na Lista:", df_prod['nome'].tolist())
        row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
        
        st.info(f"**Item:** {row['nome']} \n\n**Categoria:** {row['categoria']} \n\n**Estoque Atual:** {row['quantidade']} ({row['unidade_medida']})")
        
        if row['unidade_medida'] == 'Caixa':
            tipo_retirada = st.radio("Como deseja retirar?", ["Por Caixa", "Por Unidade"], horizontal=True)
            if tipo_retirada == "Por Caixa":
                qtd_caixas = st.number_input(f"Qtd de Caixas (cada uma com {row['qtd_por_caixa']} itens):", min_value=0.1, step=0.5, value=1.0)
                qtd_mov_final = qtd_caixas * row['qtd_por_caixa']
            else:
                qtd_mov_final = st.number_input("Qtd de Unidades soltas:", min_value=1.0, step=1.0, value=1.0)
        else:
            qtd_mov_final = st.number_input(f"Qtd a Retirar ({row['unidade_medida']}):", min_value=0.1, step=1.0, value=1.0)

        eh_epi = "EPI" in str(row['categoria']).upper() or "EPI" in str(row['nome']).upper()
        responsavel_epi = ""
        if eh_epi:
            st.warning("⚠️ **ESTE ITEM É UM EPI!**")
            responsavel_epi = st.text_input("👤 Nome/ Matrícula do Colaborador (OBRIGATÓRIO PARA EPI):")

        if st.button("Confirmar Retirada de Item", type="primary"):
            if eh_epi and not responsavel_epi.strip():
                st.error("Erro: Preencha o nome/matrícula do colaborador!")
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
    
    tipo_medida_cadastro = st.radio(
        "Como este produto será controlado no estoque?",
        ["Por Unidade (Padrão)", "Por Caixa / Embalagem"],
        horizontal=True
    )

    with st.form("form_cad_prod"):
        c1, c2 = st.columns(2)
        with c1:
            nome = st.text_input("Nome do Produto *")
            codigo_barras = st.text_input("🏷️ Código de Barras (Opcional)")
            categoria = st.text_input("Categoria (Ex: Ferramenta, Insumo, EPI)", value="Geral")
            localizacao = st.text_input("Localização / Corredor", value="Almoxarifado Principal")
            
        with c2:
            if tipo_medida_cadastro == "Por Caixa / Embalagem":
                unidade_medida = "Caixa"
                qtd_por_caixa = st.number_input("Qtd de Itens dentro de cada Caixa", min_value=1, value=1)
                num_caixas = st.number_input("Quantidade Inicial de CAIXAS", min_value=0.0, step=1.0, value=0.0)
                quantidade_total = num_caixas * qtd_por_caixa
                st.caption(f"📦 Total em estoque: **{quantidade_total:.0f} un soltas** ({num_caixas} cx x {qtd_por_caixa} un)")
            else:
                unidade_medida = st.selectbox("Unidade de Medida", ["Unidade", "Metro", "Pacote", "Rolo", "Litro"])
                qtd_por_caixa = 1
                quantidade_total = st.number_input(f"Quantidade Inicial ({unidade_medida})", min_value=0.0, step=1.0, value=0.0)

            foto = st.file_uploader("Foto do Produto (Opcional)", type=["jpg", "png", "jpeg"])

        sub = st.form_submit_button("💾 Cadastrar Produto", type="primary")
        if sub:
            if nome.strip():
                foto_path = ""
                if foto is not None:
                    foto_path = salvar_arquivo_seguro(foto, tipo="imagem")
                cadastrar_produto(nome, categoria, localizacao, quantidade_total, unidade_medida, qtd_por_caixa, foto_path, codigo_barras)
                st.success(f"Produto '{nome}' cadastrado com sucesso!")
                st.rerun()
            else:
                st.warning("O nome do produto é obrigatório!")

# --- ABA 5: ENTRADA POR NOTA FISCAL (XML) ---
elif opcao == "📄 Entrada de NF (XML Auto)":
    st.title("📄 Recebimento e Entrada por Nota Fiscal (XML)")
    
    uploaded_xml = st.file_uploader("Arraste ou selecione o arquivo .xml da Nota Fiscal:", type=["xml"])
    
    if uploaded_xml is not None:
        sucesso, dados_nfe = processar_xml_nfe(uploaded_xml)
        if sucesso:
            st.success(f"XML Lido com sucesso! Nota Fiscal Nº **{dados_nfe['numero_nf']}**")
            st.write(f"**Fornecedor:** {dados_nfe['fornecedor']} | **CNPJ:** {dados_nfe['cnpj']}")
            
            st.subheader("📋 Selecione os itens que deseja cadastrar/dar entrada:")
            st.caption("Desmarque a caixa dos itens que **NÃO** deseja importar para o estoque.")
            
            df_itens_xml = pd.DataFrame(dados_nfe['itens'])
            df_editado = st.data_editor(
                df_itens_xml,
                column_config={
                    "Importar": st.column_config.CheckboxColumn(
                        "Importar?",
                        help="Marque para cadastrar/dar entrada no estoque",
                        default=True,
                    ),
                    "produto": "Descrição do Produto (NF)",
                    "quantidade": st.column_config.NumberColumn("Qtd Recebida", format="%.2f"),
                    "valor_unitario": st.column_config.NumberColumn("Valor Unit. (R$)", format="R$ %.2f"),
                    "valor_total": st.column_config.NumberColumn("Valor Total (R$)", format="R$ %.2f"),
                },
                disabled=["produto", "quantidade", "valor_unitario", "valor_total"],
                hide_index=True,
                use_container_width=True
            )
            
            if st.button("📥 Confirmar Entrada dos Itens Selecionados", type="primary"):
                itens_para_importar = df_editado[df_editado['Importar'] == True]
                
                if itens_para_importar.empty:
                    st.warning("Nenhum item foi selecionado para importação.")
                else:
                    sucessos = 0
                    for _, row_item in itens_para_importar.iterrows():
                        ok = dar_entrada_nota_fiscal(
                            numero_nf=dados_nfe['numero_nf'],
                            fornecedor=dados_nfe['fornecedor'],
                            cnpj=dados_nfe['cnpj'],
                            nome_prod=row_item['produto'],
                            qtd_mov=float(row_item['quantidade']),
                            valor_unit=float(row_item['valor_unitario']),
                            usuario_logado=st.session_state.usuario
                        )
                        if ok:
                            sucessos += 1
                            
                    st.success(f"✅ Sucesso! {sucessos} item(ns) importado(s) e atualizado(s) no estoque!")
                    st.rerun()
        else:
            st.error(dados_nfe)

# --- ABA 6: IMPORTAR DADOS (EXCEL / SHEETS) ---
elif opcao == "📥 Importar Dados (Excel / Sheets)":
    st.title("📥 Importar Estoque em Lote via Excel")
    st.markdown("Envie uma planilha com as colunas obrigatórias: **nome**, **categoria**, **localizacao**, **quantidade**, **unidade_medida**.")
    
    arquivo_excel = st.file_uploader("Selecione o arquivo Excel (.xlsx ou .xls)", type=["xlsx", "xls"])
    if arquivo_excel is not None:
        try:
            df_imp = pd.read_excel(arquivo_excel)
            st.write("Pré-visualização dos dados:")
            st.dataframe(df_imp.head(), use_container_width=True)
            
            if st.button("Confirmar Importação de Planilha", type="primary"):
                qtd_imp = 0
                for _, r in df_imp.iterrows():
                    cadastrar_produto(
                        nome=str(r.get('nome', '')),
                        categoria=str(r.get('categoria', 'Geral')),
                        localizacao=str(r.get('localizacao', 'Almoxarifado Principal')),
                        quantidade=float(r.get('quantidade', 0)),
                        unidade_medida=str(r.get('unidade_medida', 'Unidade'))
                    )
                    qtd_imp += 1
                st.success(f"✅ {qtd_imp} produtos importados com sucesso!")
                st.rerun()
        except Exception as e:
            st.error(f"Erro ao ler arquivo Excel: {e}")

# --- ABA 7: DASHBOARD ANALYTICS BI ---
elif opcao == "📊 Dashboard Analytics (BI)":
    st.title("📊 Painel de Controle e Indicadores (BI)")
    df_p = buscar_produtos()
    df_h = buscar_historico()
    df_nf = buscar_notas_fiscais()
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"<div class='metric-card'><h4>Total de Itens</h4><h2>{len(df_p)}</h2></div>", unsafe_allow_html=True)
    with c2:
        itens_criticos = len(df_p[df_p['quantidade'] < 5]) if not df_p.empty else 0
        st.markdown(f"<div class='metric-card'><h4>Itens p/ Reposição</h4><h2>{itens_criticos}</h2></div>", unsafe_allow_html=True)
    with c3:
        total_mov = len(df_h) if not df_h.empty else 0
        st.markdown(f"<div class='metric-card'><h4>Movimentações</h4><h2>{total_mov}</h2></div>", unsafe_allow_html=True)
    with c4:
        total_nfs = len(df_nf) if not df_nf.empty else 0
        st.markdown(f"<div class='metric-card'><h4>NFs Importadas</h4><h2>{total_nfs}</h2></div>", unsafe_allow_html=True)
        
    st.write("---")
    if not df_p.empty:
        st.subheader("📈 Nível de Estoque por Produto")
        st.bar_chart(df_p.set_index('nome')['quantidade'])

# --- ABA 8: HISTÓRICO / AUDITORIA ---
elif opcao == "📜 Histórico / Auditoria":
    st.title("📜 Histórico de Movimentações e Auditoria")
    df_hist = buscar_historico()
    if not df_hist.empty:
        st.dataframe(df_hist, use_container_width=True)
        st.download_button(
            label="📥 Baixar Histórico Completo em Excel",
            data=gerar_excel_download(df_hist),
            file_name="historico_movimentacoes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Nenhuma movimentação registrada até o momento.")

# --- ABA 9: GERENCIAR USUÁRIOS (ADMIN) ---
elif opcao == "👥 Gerenciar Usuários":
    st.title("👥 Gerenciamento de Usuários do Sistema")
    df_users = buscar_usuarios()
    st.dataframe(df_users, use_container_width=True)
    
    st.write("---")
    st.subheader("➕ Criar Novo Usuário")
    with st.form("form_novo_user"):
        novo_u = st.text_input("Nome de Usuário")
        nova_s = st.text_input("Senha", type="password")
        novo_p = st.selectbox("Perfil", ["Operador", "Admin"])
        btn_u = st.form_submit_button("Cadastrar Usuário", type="primary")
        if btn_u:
            if novo_u and nova_s:
                ok, msg = cadastrar_usuario(novo_u, nova_s, novo_p)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.warning("Preencha usuário e senha!")

# --- ABA 10: PERSONALIZAR EMPRESA (ADMIN) ---
elif opcao == "⚙️ Personalizar Empresa":
    st.title("⚙️ Personalizações e Layout")
    cfg = buscar_configuracoes()
    
    with st.form("form_cfg"):
        nome_emp = st.text_input("Nome da Empresa / Almoxarifado", value=cfg['nome_empresa'])
        cor_t = st.color_picker("Cor Principal do Tema", value=cfg['cor_tema'])
        logo_f = st.file_uploader("Atualizar Logotipo (Imagem)", type=["png", "jpg", "jpeg"])
        mapa_f = st.file_uploader("Upload do Mapa/Layout do Almoxarifado (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg", "xlsx"])
        
        if st.form_submit_button("💾 Salvar Configurações", type="primary"):
            p_logo = cfg['logo_path']
            p_mapa = cfg['mapa_path']
            
            if logo_f is not None:
                p_logo = salvar_arquivo_seguro(logo_f, tipo="imagem")
            if mapa_f is not None:
                p_mapa = salvar_arquivo_seguro(mapa_f, tipo="mapa")
                
            salvar_configuracoes(nome_emp, p_logo, cor_t, p_mapa)
            st.success("Configurações atualizadas com sucesso!")
            st.rerun()
