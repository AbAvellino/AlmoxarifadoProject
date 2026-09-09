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

def salvar_registro_checklist(usuario, pdf_path, observacao=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO checklist_ferramentas (usuario, pdf_path, observacao) VALUES (%s, %s, %s)", (usuario, pdf_path, observacao))
    conn.commit()

# --- INTERFACE E NAVEGAÇÃO ---
config = buscar_configuracoes()
st.markdown(f"""
<style>
.stButton>button[kind="primary"] {{
    background-color: {config['cor_tema']};
    border-color: {config['cor_tema']};
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

opcoes_menu = [
    "📋 Consulta de Estoque",
    "🛠️ Checklist de Ferramentas",
    "📦 Retirada de Materiais",
    "➕ Cadastrar Produto",
    "📄 Entrada de NF (XML Auto)",
]

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
        st.dataframe(df_prod[['id', 'codigo_barras', 'nome', 'categoria', 'localizacao', 'unidade_medida', 'Saldo Formatado', 'Status']], use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado.")

# --- ABA 2: CHECKLIST DE FERRAMENTAS ---
elif opcao == "🛠️ Checklist de Ferramentas":
    st.title("🛠️ Checklist de Ferramentas e Equipamentos")
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

            obs_gerais = st.text_area("Observações Gerais:")
            btn_gerar_chk = st.form_submit_button("💾 Finalizar Checklist e Gerar PDF", type="primary")

        if btn_gerar_chk:
            path_pdf = gerar_pdf_checklist("Checklist Diário", st.session_state.usuario, itens_checklist, obs_gerais)
            salvar_registro_checklist(st.session_state.usuario, path_pdf, obs_gerais)
            st.success("✅ Checklist concluído!")

# --- ABA 3: RETIRADA DE MATERIAIS ---
elif opcao == "📦 Retirada de Materiais":
    st.title("📦 Retirada de Materiais")
    df_prod = buscar_produtos()
    
    if df_prod.empty:
        st.info("Nenhum produto disponível.")
    else:
        prod_selecionado = st.selectbox("Escolha o Item na Lista:", df_prod['nome'].tolist())
        row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
        
        st.info(f"**Item:** {row['nome']} | **Estoque Atual:** {row['quantidade']} ({row['unidade_medida']})")
        
        if row['unidade_medida'] == 'Caixa':
            tipo_retirada = st.radio("Como deseja retirar?", ["Por Caixa", "Por Unidade"], horizontal=True)
            if tipo_retirada == "Por Caixa":
                qtd_caixas = st.number_input(f"Qtd de Caixas ({row['qtd_por_caixa']} un/cx):", min_value=0.1, step=0.5, value=1.0)
                qtd_mov_final = qtd_caixas * row['qtd_por_caixa']
            else:
                qtd_mov_final = st.number_input("Qtd de Unidades soltas:", min_value=1.0, step=1.0, value=1.0)
        else:
            qtd_mov_final = st.number_input(f"Qtd a Retirar ({row['unidade_medida']}):", min_value=0.1, step=1.0, value=1.0)

        eh_epi = "EPI" in str(row['categoria']).upper() or "EPI" in str(row['nome']).upper()
        responsavel_epi = ""
        if eh_epi:
            responsavel_epi = st.text_input("👤 Nome/Matrícula do Colaborador (EPI Obrigatório):")

        if st.button("Confirmar Retirada", type="primary"):
            if eh_epi and not responsavel_epi.strip():
                st.error("Preencha o responsável pelo EPI!")
            else:
                ok, msg = movimentar_produto(int(row['id']), "SAÍDA", float(qtd_mov_final), float(row['quantidade']), st.session_state.usuario, responsavel_epi)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

# --- ABA 4: CADASTRO DE PRODUTO (ATUALIZADO COM OPÇÃO DE UNIDADE / CAIXA) ---
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
                qtd_por_caixa = st.number_input("Qtd de Itens que vêm DENTRO de cada Caixa", min_value=1, value=1)
                num_caixas = st.number_input("Quantidade Inicial de CAIXAS", min_value=0.0, step=1.0, value=0.0)
                quantidade_total = num_caixas * qtd_por_caixa
                st.caption(f"📦 Total em estoque calculado: **{quantidade_total:.0f} unidades soltas** ({num_caixas} cx x {qtd_por_caixa} un)")
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

# --- ABA 5: ENTRADA POR NOTA FISCAL (ATUALIZADO COM SELEÇÃO DE ITENS) ---
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
            
            # Converte os itens extraídos para um DataFrame editável
            df_itens_xml = pd.DataFrame(dados_nfe['itens'])
            
            # Tabela Interativa com Checkbox de Seleção
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
