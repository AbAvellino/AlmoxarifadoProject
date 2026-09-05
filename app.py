import hashlib
import os
import uuid
import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import psycopg2
from PIL import Image

# Configuração da página Streamlit
st.set_page_config(page_title="Sistema de Almoxarifado", layout="wide", page_icon="📦")

# Pasta para salvamento temporário
if not os.path.exists("uploads"):
    os.makedirs("uploads")

# --- MÓDULO DE SEGURANÇA ---

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

# --- CONEXÃO POOLED COM SUPABASE ---

SUPABASE_DB_URL = st.secrets.get(
    "SUPABASE_DB_URL", 
    "postgresql://postgres:SUA_SENHA@db.SEU_PROJETO.supabase.co:6543/postgres"
)

@st.cache_resource
def obter_conexao():
    """Mantém a conexão com o Supabase aberta em cache para evitar atrasos na reconexão."""
    return psycopg2.connect(SUPABASE_DB_URL)

def conectar():
    try:
        conn = obter_conexao()
        if conn.closed != 0:
            st.cache_resource.clear()
            return obter_conexao()
        return conn
    except Exception:
        st.cache_resource.clear()
        return obter_conexao()

def inicializar_banco():
    conn = conectar()
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

        # Garante coluna mapa_path se tabela ja existia
        cursor.execute("ALTER TABLE configuracoes ADD COLUMN IF NOT EXISTS mapa_path TEXT DEFAULT '';")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                categoria TEXT DEFAULT 'Geral',
                localizacao TEXT DEFAULT 'Não informada',
                quantidade REAL NOT NULL DEFAULT 0,
                unidade_medida TEXT DEFAULT 'Caixa',
                qtd_por_caixa INTEGER DEFAULT 1,
                foto_path TEXT DEFAULT ''
            );
        """)

        # Garante coluna localizacao se tabela ja existia
        cursor.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS localizacao TEXT DEFAULT 'Não informada';")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                produto_id INTEGER REFERENCES produtos(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL,
                quantidade REAL NOT NULL,
                usuario TEXT DEFAULT 'Sistema',
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                usuario TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL
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

        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("admin", gerar_hash_senha("1234"), "Admin"))
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("operador", gerar_hash_senha("1234"), "Operador"))

        conn.commit()

# Inicializa o banco uma única vez na inicialização
if 'banco_inicializado' not in st.session_state:
    inicializar_banco()
    st.session_state.banco_inicializado = True

# --- CACHING DE CONSULTAS ---

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

@st.cache_data(ttl=60)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path FROM produtos ORDER BY id ASC", conn)

def cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida="Caixa", qtd_por_caixa=1, foto_path=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO produtos (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path)
        )
        conn.commit()
    st.cache_data.clear()

def editar_produto(prod_id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path=""):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE produtos SET nome = %s, categoria = %s, localizacao = %s, quantidade = %s, unidade_medida = %s, qtd_por_caixa = %s, foto_path = %s WHERE id = %s", 
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, prod_id)
        )
        conn.commit()
    st.cache_data.clear()

def excluir_produto(prod_id):
    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM produtos WHERE id = %s", (prod_id,))
        conn.commit()
    st.cache_data.clear()

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado):
    if tipo == "SAÍDA" and qtd_mov > qtd_atual:
        return False, f"Estoque insuficiente! Saldo atual: {qtd_atual:.2f}"

    nova_qtd = qtd_atual + qtd_mov if tipo == "ENTRADA" else qtd_atual - qtd_mov

    conn = conectar()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE produtos SET quantidade = %s WHERE id = %s", (nova_qtd, prod_id))
        cursor.execute(
            "INSERT INTO historico (produto_id, tipo, quantidade, usuario) VALUES (%s, %s, %s, %s)",
            (prod_id, tipo, qtd_mov, usuario_logado)
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

            itens.append({
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
    return pd.read_sql_query("""
        SELECT id, numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, 
               valor_unitario, valor_total, data_recebimento, usuario
        FROM notas_fiscais
        ORDER BY id DESC
    """, conn)

# --- GESTÃO DE USUÁRIOS ---

@st.cache_data(ttl=60)
def buscar_usuarios():
    conn = conectar()
    return pd.read_sql_query("SELECT id, usuario, perfil FROM usuarios ORDER BY id ASC", conn)

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

def editar_usuario_admin(usr_id, novo_nome, novo_perfil, nova_senha=""):
    try:
        conn = conectar()
        with conn.cursor() as cursor:
            if nova_senha.strip():
                hash_s = gerar_hash_senha(nova_senha)
                cursor.execute("UPDATE usuarios SET usuario = %s, perfil = %s, senha = %s WHERE id = %s", (novo_nome, novo_perfil, hash_s, usr_id))
            else:
                cursor.execute("UPDATE usuarios SET usuario = %s, perfil = %s WHERE id = %s", (novo_nome, novo_perfil, usr_id))
            conn.commit()
            st.cache_data.clear()
            return True, "Usuário atualizado com sucesso!"
    except Exception as e:
        return False, f"Erro ao atualizar usuário: {e}"

def excluir_usuario(usr_id):
    try:
        conn = conectar()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM usuarios WHERE id = %s", (usr_id,))
            conn.commit()
            st.cache_data.clear()
            return True, "Usuário excluído com sucesso!"
    except Exception as e:
        return False, f"Erro ao excluir usuário: {e}"

@st.cache_data(ttl=60)
def buscar_historico():
    conn = conectar()
    return pd.read_sql_query("""
        SELECT h.id, p.nome as produto, h.tipo, h.quantidade, h.usuario, h.data_hora 
        FROM historico h
        LEFT JOIN produtos p ON h.produto_id = p.id
        ORDER BY h.id DESC
    """, conn)

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

# SESSÃO DE AUTENTICAÇÃO
if 'logado' not in st.session_state:
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""

if not st.session_state.logado:
    st.title(f"🔒 {config['nome_empresa']}")
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
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos!")
else:
    if config['logo_path'] and os.path.exists(config['logo_path']):
        st.sidebar.image(config['logo_path'], use_container_width=True)
    st.sidebar.title(config['nome_empresa'])
    st.sidebar.write(f"👤 **{st.session_state.usuario}** ({st.session_state.perfil})")

    # Visualização do Mapa / Layout do Almoxarifado no Menu
    if config['mapa_path'] and os.path.exists(config['mapa_path']):
        st.sidebar.write("---")
        st.sidebar.subheader("🗺️ Layout / Mapa")
        ext = os.path.splitext(config['mapa_path'])[1].lower()
        
        with open(config['mapa_path'], "rb") as f:
            bytes_file = f.read()
        
        st.sidebar.download_button(
            label="📄 Baixar Mapa do Almoxarifado",
            data=bytes_file,
            file_name=f"mapa_almoxarifado{ext}",
            mime="application/pdf" if ext == ".pdf" else "application/octet-stream"
        )
    
    opcoes_menu = [
        "📦 Estoque & Movimentação",
        "🧾 Entrada de NF (XML Auto)",
        "📥 Importar Dados (Excel / Sheets)",
        "📊 Dashboard Analytics",
        "📋 Histórico / Auditoria",
        "➕ Cadastrar Produto"
    ]
    if st.session_state.perfil == "Admin":
        opcoes_menu.append("👥 Gerenciar Usuários")
        opcoes_menu.append("⚙️ Personalizar Empresa")

    opcao = st.sidebar.radio("Navegação", opcoes_menu)

    if st.sidebar.button("Sair / Logout"):
        st.session_state.logado = False
        st.session_state.usuario = ""
        st.session_state.perfil = ""
        st.rerun()

    # --- ABA 1: ESTOQUE E MOVIMENTAÇÃO ---
    if opcao == "📦 Estoque & Movimentação":
        st.title(f"📦 Controle de Estoque - {config['nome_empresa']}")

        df_prod = buscar_produtos()

        if not df_prod.empty:
            df_prod['Status'] = df_prod['quantidade'].apply(lambda x: "⚠️ REPOR" if x < 5 else "OK")
            
            def formatar_saldo(row):
                if row['unidade_medida'] == 'Caixa':
                    qtd_cx = row['quantidade'] / row['qtd_por_caixa'] if row['qtd_por_caixa'] > 0 else 0
                    return f"{row['quantidade']:.0f} un ({qtd_cx:.2f} CX)"
                else:
                    return f"{row['quantidade']:.2f} Mts"

            df_prod['Saldo Formatado'] = df_prod.apply(formatar_saldo, axis=1)
        else:
            df_prod['Status'] = pd.Series(dtype='str')
            df_prod['Saldo Formatado'] = pd.Series(dtype='str')

        c_busca1, c_busca2 = st.columns([2, 1])
        with c_busca1:
            busca = st.text_input("🔍 Buscar por nome do produto:")
        with c_busca2:
            busca_loc = st.text_input("📍 Filtrar por Localização / Setor:")

        if not df_prod.empty:
            if busca:
                df_prod = df_prod[df_prod['nome'].str.contains(busca, case=False, na=False)]
            if busca_loc:
                df_prod = df_prod[df_prod['localizacao'].str.contains(busca_loc, case=False, na=False)]

        st.dataframe(df_prod[['id', 'nome', 'categoria', 'localizacao', 'unidade_medida', 'qtd_por_caixa', 'Saldo Formatado', 'Status']], use_container_width=True)

        if df_prod.empty:
            st.info("Nenhum produto cadastrado ou localizado no filtro. Acesse '➕ Cadastrar Produto' para começar!")

        st.subheader("🖼️ Galeria Visual de Produtos & Localização")
        if not df_prod.empty:
            cols = st.columns(4)
            for idx, row in df_prod.reset_index(drop=True).iterrows():
                col = cols[idx % 4]
                with col:
                    if row['foto_path'] and os.path.exists(row['foto_path']):
                        st.image(row['foto_path'], use_container_width=True)
                    else:
                        st.caption("📷 *Sem Foto*")
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"📍 Loc: **{row['localizacao']}**")
                    st.caption(f"Estoque: {row['Saldo Formatado']} | Cat: {row['categoria']}")

        st.write("---")
        st.subheader("🔄 Realizar Movimentação Manual")
        if not df_prod.empty:
            item_selecionado = st.selectbox("Selecione o Produto para Movimentar:", df_prod['nome'].tolist())
            row = df_prod[df_prod['nome'] == item_selecionado].iloc[0]
            
            c1, c2, c3 = st.columns(3)
            with c1:
                tipo_mov = st.selectbox("Tipo de Operação:", ["ENTRADA", "SAÍDA"])
            
            with c2:
                if row['unidade_medida'] == 'Caixa':
                    tipo_entrada = st.radio("Movimentar por:", ["Quantidade de Itens Avulsos", "Quantidade de Caixas Inteiras"])
                    if tipo_entrada == "Quantidade de Caixas Inteiras":
                        qtd_cx_mov = st.number_input("Qtd de Caixas:", min_value=0.1, step=0.5, value=1.0)
                        qtd_mov_final = qtd_cx_mov * row['qtd_por_caixa']
                        st.caption(f"💡 Equivale a **{qtd_mov_final:.0f} itens** un/rolos.")
                    else:
                        qtd_mov_final = st.number_input("Qtd de Itens:", min_value=1, step=1, value=1)
                else:
                    qtd_mov_final = st.number_input("Metros (Mts):", min_value=0.1, step=0.5, value=1.0)

            with c3:
                st.write(" ")
                st.write(" ")
                if st.button("Confirmar Movimentação", type="primary"):
                    ok, msg = movimentar_produto(
                        int(row['id']), tipo_mov, float(qtd_mov_final), float(row['quantidade']), st.session_state.usuario
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        if st.session_state.perfil == "Admin" and not df_prod.empty:
            st.write("---")
            st.subheader("⚙️ Gerenciar Produtos, Localização e Fotos (Exclusivo Admin)")
            
            p_sel = st.selectbox("Selecione um Produto para Editar ou Excluir:", df_prod['nome'].tolist(), key="admin_edit_prod")
            row_p = df_prod[df_prod['nome'] == p_sel].iloc[0]

            with st.expander(f"Editar / Excluir: {row_p['nome']}"):
                with st.form("form_edit_prod"):
                    e_nome = st.text_input("Nome do Produto", value=row_p['nome'])
                    e_cat = st.text_input("Categoria", value=row_p['categoria'])
                    e_loc = st.text_input("Localização / Endereçamento", value=row_p.get('localizacao', 'Não informada'))
                    e_unidade = st.selectbox("Unidade de Medida", ["Caixa", "Metro"], index=0 if row_p['unidade_medida'] == 'Caixa' else 1)
                    
                    e_qtd_caixa = 1
                    if e_unidade == "Caixa":
                        e_qtd_caixa = st.number_input("Qtd de Itens por Caixa", value=int(row_p['qtd_por_caixa']), min_value=1)
                    
                    e_qtd = st.number_input("Quantidade Total Atual em Estoque (em Itens ou Metros)", value=float(row_p['quantidade']), min_value=0.0)
                    e_foto = st.file_uploader("Atualizar Foto do Produto", type=["jpg", "png", "jpeg"])

                    btn_salvar = st.form_submit_button("💾 Salvar Alterações")
                    
                if btn_salvar:
                    foto_path = row_p['foto_path']
                    if e_foto is not None:
                        foto_path = salvar_arquivo_seguro(e_foto, tipo="imagem")

                    editar_produto(int(row_p['id']), e_nome, e_cat, e_loc, float(e_qtd), e_unidade, int(e_qtd_caixa), foto_path)
                    st.success("Produto atualizado com sucesso!")
                    st.rerun()

                if st.button("🗑️ Excluir Produto", type="secondary"):
                    excluir_produto(int(row_p['id']))
                    st.warning("Produto excluído!")
                    st.rerun()

    # --- ABA 2: ENTRADA POR NOTA FISCAL ---
    elif opcao == "🧾 Entrada de NF (XML Auto)":
        st.title("🧾 Recebimento e Entrada por Nota Fiscal")

        aba_xml, aba_manual = st.tabs(["📂 Importar Arquivo XML (Automático)", "✍️ Lançamento Manual"])

        with aba_xml:
            st.subheader("Upload de Arquivo XML da NF-e")
            uploaded_xml = st.file_uploader("Arraste ou selecione o arquivo .xml da Nota Fiscal:", type=["xml"])

            if uploaded_xml is not None:
                sucesso, dados_nfe = processar_xml_nfe(uploaded_xml)

                if sucesso:
                    st.success(f"XML lido com sucesso! Nota Fiscal Nº **{dados_nfe['numero_nf']}**")
                    st.write(f"**Fornecedor:** {dados_nfe['fornecedor']} | **CNPJ:** {dados_nfe['cnpj']}")

                    st.write("### Itens detectados na Nota Fiscal:")
                    df_itens = pd.DataFrame(dados_nfe['itens'])
                    st.dataframe(df_itens, use_container_width=True)

                    if st.button("🚀 Confirmar e Dar Entrada Automática no Estoque", type="primary"):
                        count_sucesso = 0
                        for item in dados_nfe['itens']:
                            dar_entrada_nota_fiscal(
                                dados_nfe['numero_nf'],
                                dados_nfe['fornecedor'],
                                dados_nfe['cnpj'],
                                item['produto'],
                                float(item['quantidade']),
                                float(item['valor_unitario']),
                                st.session_state.usuario
                            )
                            count_sucesso += 1

                        st.success(f"Entrada concluída! {count_sucesso} itens atualizados/cadastrados no estoque.")
                        st.rerun()
                else:
                    st.error(dados_nfe)

        with aba_manual:
            st.subheader("➕ Lançamento Manual de Nota Fiscal")
            
            with st.form("form_nf_manual"):
                c1, c2 = st.columns(2)
                with c1:
                    num_nf = st.text_input("Número da Nota Fiscal")
                    fornecedor = st.text_input("Fornecedor / Empresa")
                    cnpj = st.text_input("CNPJ (Opcional)")
                    nome_prod = st.text_input("Nome do Produto (Se for novo, será cadastrado)")
                
                with c2:
                    qtd_nf = st.number_input("Quantidade Recebida (Itens ou Metros)", min_value=0.1, step=1.0)
                    val_unit = st.number_input("Valor Unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
                    st.write(f"**Valor Total Estimado:** R$ {qtd_nf * val_unit:.2f}")

                if st.form_submit_button("Salvar e Dar Entrada"):
                    if num_nf and fornecedor and nome_prod:
                        dar_entrada_nota_fiscal(
                            num_nf, fornecedor, cnpj, nome_prod, float(qtd_nf), float(val_unit), st.session_state.usuario
                        )
                        st.success("Nota Fiscal lançada e estoque atualizado com sucesso!")
                        st.rerun()
                    else:
                        st.warning("Preencha o Número da NF, Fornecedor e Nome do Produto!")

        st.write("---")
        st.subheader("📋 Registros de Notas Fiscais Lançadas")
        df_nf = buscar_notas_fiscais()
        st.dataframe(df_nf, use_container_width=True)

    # --- ABA 3: IMPORTAR EXCEL OU GOOGLE SHEETS ---
    elif opcao == "📥 Importar Dados (Excel / Sheets)":
        st.title("📥 Importação em Lote de Produtos")

        tab_excel, tab_sheets = st.tabs(["📊 Importar de Planilha Excel (.xlsx)", "🌐 Importar de Google Sheets"])

        with tab_excel:
            st.subheader("Upload de Arquivo Excel")
            st.caption("Colunas suportadas: **nome**, **categoria**, **localizacao**, **quantidade**, **unidade_medida**, **qtd_por_caixa**")

            file_excel = st.file_uploader("Selecione o arquivo .xlsx:", type=["xlsx", "xls"])
            if file_excel is not None:
                try:
                    df_imp = pd.read_excel(file_excel)
                    st.write("### Pré-visualização dos dados:")
                    st.dataframe(df_imp, use_container_width=True)

                    if st.button("🚀 Confirmar e Importar para o Supabase", type="primary"):
                        qtd_importados = 0
                        for idx, row in df_imp.iterrows():
                            cadastrar_produto(
                                str(row['nome']), 
                                str(row.get('categoria', 'Geral')),
                                str(row.get('localizacao', 'Não informada')),
                                float(row.get('quantidade', 0)),
                                str(row.get('unidade_medida', 'Caixa')),
                                int(row.get('qtd_por_caixa', 1))
                            )
                            qtd_importados += 1
                        st.success(f"{qtd_importados} produtos cadastrados no Supabase com sucesso!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro ao ler arquivo Excel: {e}")

        with tab_sheets:
            st.subheader("Sincronização com Google Sheets (Público)")
            st.caption("Cole o ID da planilha do Google Planilhas (compartilhada como 'Qualquer pessoa com o link').")

            sheet_id = st.text_input("ID da Planilha do Google Sheets:")
            if sheet_id:
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
                try:
                    df_gsheets = pd.read_csv(url)
                    st.write("### Dados carregados da nuvem:")
                    st.dataframe(df_gsheets, use_container_width=True)

                    if st.button("📥 Importar Dados para o Supabase", type="primary"):
                        qtd_importados = 0
                        for idx, row in df_gsheets.iterrows():
                            cadastrar_produto(
                                str(row['nome']), 
                                str(row.get('categoria', 'Geral')),
                                str(row.get('localizacao', 'Não informada')),
                                float(row.get('quantidade', 0)),
                                str(row.get('unidade_medida', 'Caixa')),
                                int(row.get('qtd_por_caixa', 1))
                            )
                            qtd_importados += 1
                        st.success(f"{qtd_importados} produtos importados para o Supabase!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível acessar a planilha: {e}")

    # --- ABA 4: DASHBOARD ANALYTICS ---
    elif opcao == "📊 Dashboard Analytics":
        st.title("📊 Painel de Indicadores (BI)")

        df_prod = buscar_produtos()

        if not df_prod.empty:
            col1, col2, col3 = st.columns(3)
            col1.metric("Total de Produtos", len(df_prod))
            col2.metric("Total de Itens / Metros em Estoque", f"{df_prod['quantidade'].sum():.2f}")
            col3.metric("Itens Críticos (Reposição)", len(df_prod[df_prod['quantidade'] < 5]))

            st.write("---")

            col_g1, col_g2 = st.columns(2)

            with col_g1:
                st.subheader("Quantidade Total por Produto")
                fig1, ax1 = plt.subplots()
                ax1.bar(df_prod['nome'], df_prod['quantidade'], color=config['cor_tema'])
                plt.xticks(rotation=45, ha='right')
                ax1.set_ylabel("Quantidade (Un/Mts)")
                plt.tight_layout()
                st.pyplot(fig1)

            with col_g2:
                st.subheader("Distribuição por Categoria")
                cat_df = df_prod.groupby('categoria')['quantidade'].sum()
                fig2, ax2 = plt.subplots()
                ax2.pie(cat_df, labels=cat_df.index, autopct='%1.1f%%', startangle=90)
                plt.tight_layout()
                st.pyplot(fig2)
        else:
            st.info("Cadastre produtos para visualizar os gráficos.")

    # --- ABA 5: HISTÓRICO ---
    elif opcao == "📋 Histórico / Auditoria":
        st.title("📋 Histórico de Movimentações")
        df_hist = buscar_historico()
        st.dataframe(df_hist, use_container_width=True)

    # --- ABA 6: CADASTRAR PRODUTO ---
    elif opcao == "➕ Cadastrar Produto":
        st.title("➕ Cadastrar Novo Produto")
        if st.session_state.perfil != "Admin":
            st.error("Apenas Administradores podem cadastrar produtos!")
        else:
            with st.form("form_cad"):
                c_a, c_b = st.columns(2)
                with c_a:
                    nome = st.text_input("Nome do Produto")
                    cat = st.text_input("Categoria", value="Geral")
                    loc = st.text_input("Localização / Endereçamento no Almoxarifado", placeholder="Ex: Rua A, Prateleira 3, Gaiola 02")
                    unidade = st.selectbox("Unidade de Medida", ["Caixa", "Metro"])
                
                with c_b:
                    if unidade == "Caixa":
                        qtd_por_caixa = st.number_input("Quantas unidades formam 1 Caixa?", min_value=1, value=48, step=1)
                        qtd_inicial_caixas = st.number_input("Quantidade Inicial de Caixas em Estoque:", value=0, disabled=True, help="O estoque inicia em 0 caixas por padrão.")
                        qtd_itens_input = 0.0
                    else:
                        qtd_por_caixa = 1
                        qtd_itens_input = st.number_input("Metros Iniciais em Estoque:", min_value=0.0, step=0.5, value=0.0)

                foto = st.file_uploader("Foto do Produto (Opcional)", type=["jpg", "png", "jpeg"])

                if st.form_submit_button("Cadastrar Produto"):
                    if nome:
                        foto_path = ""
                        if foto is not None:
                            foto_path = salvar_arquivo_seguro(foto, tipo="imagem")
                        
                        cadastrar_produto(
                            nome, cat, loc if loc else "Não informada", qtd_itens_input, unidade, int(qtd_por_caixa), foto_path
                        )
                        st.success(f"Produto '{nome}' cadastrado com sucesso no Supabase!")
                        st.rerun()
                    else:
                        st.warning("Preencha o nome do produto!")

    # --- ABA 7: GERENCIAR USUÁRIOS (ADMINISTRADORES) ---
    elif opcao == "👥 Gerenciar Usuários":
        st.title("👥 Gerenciamento Completo de Usuários")
        
        st.subheader("➕ Cadastrar Novo Usuário")
        with st.form("form_novo_user"):
            u_nome = st.text_input("Nome do Usuário")
            u_senha = st.text_input("Senha", type="password")
            u_perfil = st.selectbox("Perfil de Acesso", ["Operador", "Admin"])
            if st.form_submit_button("Salvar Novo Usuário"):
                if u_nome and u_senha:
                    ok, msg = cadastrar_usuario(u_nome, u_senha, u_perfil)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Preencha o nome de usuário e a senha!")

        st.write("---")
        st.subheader("📋 Usuários Cadastrados no Supabase")
        df_users = buscar_usuarios()
        st.dataframe(df_users, use_container_width=True)

        st.write("---")
        st.subheader("✏️ Editar ou 🗑️ Excluir Usuário")
        if not df_users.empty:
            usr_selecionado = st.selectbox("Selecione o Usuário:", df_users['usuario'].tolist())
            row_u = df_users[df_users['usuario'] == usr_selecionado].iloc[0]

            col_ed1, col_ed2 = st.columns(2)
            with col_ed1:
                with st.form("form_edit_user"):
                    e_usr_nome = st.text_input("Nome do Usuário", value=row_u['usuario'])
                    e_usr_perfil = st.selectbox("Perfil de Acesso", ["Operador", "Admin"], index=0 if row_u['perfil'] == "Operador" else 1)
                    e_usr_nova_senha = st.text_input("Nova Senha (deixe em branco para manter a atual)", type="password")
                    
                    if st.form_submit_button("💾 Salvar Alterações do Usuário"):
                        ok, msg = editar_usuario_admin(int(row_u['id']), e_usr_nome, e_usr_perfil, e_usr_nova_senha)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

            with col_ed2:
                st.write("### Excluir Usuário")
                st.caption("A exclusão é permanente e removerá o acesso deste usuário ao sistema.")
                if st.button(f"🗑️ Excluir Usuário '{row_u['usuario']}'", type="secondary"):
                    ok, msg = excluir_usuario(int(row_u['id']))
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    # --- ABA 8: PERSONALIZAR EMPRESA (ADMIN) ---
    elif opcao == "⚙️ Personalizar Empresa":
        st.title("⚙️ Personalização do Sistema e Layout")
        
        with st.form("form_config"):
            emp_nome = st.text_input("Nome da Empresa", value=config['nome_empresa'])
            emp_cor = st.color_picker("Cor Principal do Tema", value=config['cor_tema'])
            emp_logo = st.file_uploader("Atualizar Logomarca da Empresa", type=["png", "jpg", "jpeg"])
            emp_mapa = st.file_uploader("Anexar Mapa / Layout de Setores do Almoxarifado (PDF, Excel ou Imagem)", type=["pdf", "png", "jpg", "jpeg", "xlsx", "xls"])
            
            if st.form_submit_button("Salvar Configurações"):
                logo_path = config['logo_path']
                mapa_path = config['mapa_path']

                if emp_logo is not None:
                    logo_path = salvar_arquivo_seguro(emp_logo, tipo="imagem")
                
                if emp_mapa is not None:
                    mapa_path = salvar_arquivo_seguro(emp_mapa, tipo="mapa")

                salvar_configuracoes(emp_nome, logo_path, emp_cor, mapa_path)
                st.success("Configurações do sistema e mapa atualizados com sucesso!")
                st.rerun()

        # Pré-visualização do Mapa Atual
        if config['mapa_path'] and os.path.exists(config['mapa_path']):
            st.write("---")
            st.subheader("📌 Mapa / Layout Atual Anotado")
            ext = os.path.splitext(config['mapa_path'])[1].lower()
            if ext in [".png", ".jpg", ".jpeg", ".webp"]:
                st.image(config['mapa_path'], caption="Mapa do Almoxarifado", use_container_width=True)
            elif ext == ".pdf":
                st.info("O mapa do almoxarifado está salvo em formato **PDF**. Os usuários podem baixá-lo no menu lateral.")
            else:
                st.info(f"O mapa do almoxarifado está salvo em formato **{ext.upper()}**. Disponível para download no menu lateral.")
