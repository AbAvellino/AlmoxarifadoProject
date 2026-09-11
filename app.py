import datetime
import glob
import hashlib
import io
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from fpdf import FPDF
from google import genai
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import streamlit as st

# Configuração da página Streamlit
st.set_page_config(page_title="Sistema de Almoxarifado", layout="wide", page_icon="🦊")

# --- CSS PARA OCULTAR ELEMENTOS PADRÃO DO STREAMLIT ---
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    .stAppHeader {display: none;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Pastas para salvamento temporário
for pasta in ["uploads", "relatorios_checklist"]:
    if not os.path.exists(pasta):
        os.makedirs(pasta)

# --- INICIALIZAÇÃO DA API DO GEMINI (FOX ASSISTENTE) ---
@st.cache_resource
def init_gemini():
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

client_gemini = init_gemini()

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

# --- SEGURANÇA ---
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
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    permitidas = EXTENSOES_PERMITIDAS_XML if tipo == "xml" else (EXTENSOES_PERMITIDAS_MAPA if tipo == "mapa" else EXTENSOES_PERMITIDAS_IMAGEM)
    if ext not in permitidas:
        raise ValueError(f"Extensão não permitida: {ext}")
    novo_nome = f"{uuid.uuid4().hex}{ext}"
    caminho_completo = os.path.join(pasta_destino, novo_nome)
    with open(caminho_completo, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return caminho_completo

def gerar_excel_download(df: pd.DataFrame, nome_arquivo="relatorio.xlsx") -> bytes:
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Relatório')
    except Exception:
        with pd.ExcelWriter(output) as writer:
            df.to_excel(writer, index=False, sheet_name='Relatório')
    return output.getvalue()

def gerenciar_limpeza_pdf_pasta(pasta="relatorios_checklist"):
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

# --- INICIALIZAÇÃO E MIGRAÇÃO DO BANCO ---
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
                codigo_barras TEXT DEFAULT '',
                ca TEXT DEFAULT '',
                qtd_minima REAL DEFAULT 5.0
            );
            """)
            
            # Garantir tabela de projetos
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS projetos (
                id SERIAL PRIMARY KEY,
                nome_projeto TEXT UNIQUE NOT NULL,
                descricao TEXT DEFAULT '',
                equipe TEXT DEFAULT '',
                status TEXT DEFAULT 'Ativo',
                data_inicio DATE DEFAULT CURRENT_DATE
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
                quem_retirou TEXT DEFAULT '',
                projeto_destino TEXT DEFAULT '',
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            
            # Garantir colunas no historico em caso de tabela legada
            cursor.execute("ALTER TABLE historico ADD COLUMN IF NOT EXISTS quem_retirou TEXT DEFAULT '';")
            cursor.execute("ALTER TABLE historico ADD COLUMN IF NOT EXISTS projeto_destino TEXT DEFAULT '';")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                usuario TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL,
                ultima_atividade TIMESTAMP,
                localizacao_sessao TEXT DEFAULT 'Almoxarifado Principal',
                acao_atual TEXT DEFAULT 'Navegando no Sistema'
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

            # Zerar informações após 18 meses (540 dias)
            cursor.execute("""
            UPDATE historico 
            SET quem_retirou = 'EXPIRADO (18 MESES)', projeto_destino = 'EXPIRADO (18 MESES)'
            WHERE data_hora < NOW() - INTERVAL '540 days';
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

# Executa migração/inicialização do banco
inicializar_banco()

# --- CONSULTAS BANCO DE DADOS ---
def atualizar_presenca_usuario(usuario, localizacao="Almoxarifado Principal", acao="Navegando no Sistema"):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE usuarios SET ultima_atividade = NOW(), localizacao_sessao = %s, acao_atual = %s WHERE usuario = %s", (localizacao, acao, usuario))
            conn.commit()
    except Exception:
        conn.rollback()

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
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE configuracoes SET nome_empresa = %s, logo_path = %s, cor_tema = %s, mapa_path = %s WHERE id = 1", (nome, logo_path, cor, mapa_path))
            conn.commit()
            st.cache_data.clear()
            return True, "Configurações salvas com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao salvar configurações: {e}"

def autenticar_usuario(usuario, senha_digitada):
    conn = conectar()
    try:
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
    except Exception:
        conn.rollback()
        return None

@st.cache_data(ttl=10)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima FROM produtos ORDER BY id ASC", conn)

@st.cache_data(ttl=10)
def buscar_projetos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome_projeto, descricao, equipe, status, data_inicio FROM projetos ORDER BY id DESC", conn)

def cadastrar_projeto(nome_projeto, descricao, equipe):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO projetos (nome_projeto, descricao, equipe) VALUES (%s, %s, %s)", (nome_projeto, descricao, equipe))
            conn.commit()
            st.cache_data.clear()
            return True, "Projeto cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar projeto: {e}"

def cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida="Caixa", qtd_por_caixa=1, foto_path="", codigo_barras="", ca="", qtd_minima=5.0):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO produtos (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima)
            )
            conn.commit()
            st.cache_data.clear()
            return True, f"Produto '{nome}' cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar produto: {e}"

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado, responsavel_epi="", quem_retirou="", projeto_destino=""):
    tipo_upper = tipo.upper()
    if tipo_upper == "SAÍDA" and qtd_mov > qtd_atual:
        return False, f"Estoque insuficiente! Saldo atual: {qtd_atual:.2f}"
    if tipo_upper in ["ENTRADA", "DEVOLUÇÃO", "DEVOLUCAO"]:
        nova_qtd = qtd_atual + qtd_mov
    else:
        nova_qtd = qtd_atual - qtd_mov
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE produtos SET quantidade = %s WHERE id = %s", (nova_qtd, prod_id))
            cursor.execute(
                "INSERT INTO historico (produto_id, tipo, quantidade, usuario, responsavel_epi, quem_retirou, projeto_destino) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (prod_id, tipo_upper, qtd_mov, usuario_logado, responsavel_epi, quem_retirou, projeto_destino)
            )
            conn.commit()
            st.cache_data.clear()
            return True, "Movimentação realizada com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao registrar movimentação: {e}"

@st.cache_data(ttl=10)
def buscar_historico():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT h.id, p.nome as produto, p.categoria, h.tipo, h.quantidade, h.usuario, h.responsavel_epi, h.quem_retirou, h.projeto_destino, h.data_hora
    FROM historico h LEFT JOIN produtos p ON h.produto_id = p.id ORDER BY h.id DESC
    """, conn)

@st.cache_data(ttl=10)
def buscar_notas_fiscais():
    conn = conectar()
    return pd.read_sql_query("SELECT id, numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, valor_unitario, valor_total, data_recebimento, usuario FROM notas_fiscais ORDER BY id DESC", conn)

def gerar_contexto_dados_sistema():
    df_p = buscar_produtos()
    df_nf = buscar_notas_fiscais()
    df_h = buscar_historico()
    df_proj = buscar_projetos()
    
    resumo = "=== CONTEXTO EM TEMPO REAL DO ALMOXARIFADO ===\n"
    resumo += f"- Total de Produtos Cadastrados: {len(df_p)}\n"
    
    if not df_p.empty:
        criticos = df_p[df_p['quantidade'] <= df_p['qtd_minima']]
        resumo += f"- Produtos em Nível Crítico/Zerado ({len(criticos)}): {', '.join(criticos['nome'].tolist()[:10])}\n"
        resumo += "- Tabela Resumida de Produtos e Saldos:\n"
        resumo += df_p[['id', 'nome', 'categoria', 'quantidade', 'unidade_medida']].to_string(index=False) + "\n\n"
        
    if not df_nf.empty:
        resumo += "- Preços e Compras Recentes (Notas Fiscais):\n"
        resumo += df_nf[['numero_nf', 'fornecedor', 'produto_nome', 'quantidade', 'valor_unitario', 'valor_total', 'data_recebimento']].head(15).to_string(index=False) + "\n\n"
        
    if not df_proj.empty:
        resumo += "- Projetos Cadastrados:\n"
        resumo += df_proj[['nome_projeto', 'equipe', 'status']].to_string(index=False) + "\n\n"
        
    if not df_h.empty:
        saidas = df_h[df_h['tipo'] == 'SAÍDA']
        if not saidas.empty:
            resumo += "- Resumo de Saídas por Projeto:\n"
            group_p = saidas.groupby('projeto_destino')['quantidade'].sum().reset_index()
            resumo += group_p.to_string(index=False) + "\n\n"
            
    return resumo

# --- INTERFACE E NAVEGAÇÃO ---
config = buscar_configuracoes()

if 'logado' not in st.session_state:
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""

if not st.session_state.logado:
    st.title(f"🏢 {config['nome_empresa']}")
    col1, _ = st.columns([1, 2])
    with col1:
        user_input = st.text_input("👤 Usuário")
        pass_input = st.text_input("🔑 Senha", type="password")
        if st.button("🚀 Entrar", type="primary"):
            perfil = autenticar_usuario(user_input, pass_input)
            if perfil:
                st.session_state.logado = True
                st.session_state.usuario = user_input
                st.session_state.perfil = perfil
                atualizar_presenca_usuario(user_input, acao="Efetuou Login")
                st.rerun()
            else:
                st.error("❌ Usuário ou senha incorretos!")
    st.stop()

opcoes_menu = [
    "📦 Consulta de Estoque",
    "🏗️ Projetos & Equipes",
    "🔄 Retirada / Devolução de Materiais",
    "📊 Dashboard Analytics (BI)",
    "📜 Histórico / Auditoria",
    "🦊 Fox Assistente"
]

st.sidebar.title(f"🏢 {config['nome_empresa']}")
st.sidebar.write(f"👤 **{st.session_state.usuario}** ({st.session_state.perfil})")

opcao = st.sidebar.radio("📍 Navegação", opcoes_menu)

if st.sidebar.button("🚪 Sair / Logout"):
    st.session_state.logado = False
    st.rerun()

if "Consulta de Estoque" in opcao:
    st.title("📦 Consulta de Estoque")
    df_prod = buscar_produtos()
    st.dataframe(df_prod, use_container_width=True)

elif "Projetos & Equipes" in opcao:
    st.title("🏗️ Gestão de Projetos e Colaboradores")
    tab_p1, tab_p2 = st.tabs(["📋 Projetos Cadastrados", "➕ Cadastrar Novo Projeto"])
    
    with tab_p1:
        df_proj = buscar_projetos()
        if df_proj.empty:
            st.info("Nenhum projeto cadastrado até o momento.")
        else:
            st.dataframe(df_proj, use_container_width=True)
            
    with tab_p2:
        with st.form("form_cad_projeto", clear_on_submit=True):
            p_nome = st.text_input("🏗️ Nome do Projeto *")
            p_desc = st.text_area("📝 Descrição do Projeto")
            p_equipe = st.text_area("👥 Colaboradores / Equipe Atribuída")
            if st.form_submit_button("💾 Cadastrar Projeto", type="primary"):
                if p_nome.strip():
                    ok, msg = cadastrar_projeto(p_nome, p_desc, p_equipe)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

elif "Retirada / Devolução" in opcao:
    st.title("🔄 Retirada / Devolução de Materiais")
    df_prod = buscar_produtos()
    df_proj = buscar_projetos()
    
    lista_projetos = df_proj['nome_projeto'].tolist() if not df_proj.empty else ["Uso Geral / Manutenção Interna"]
    
    if df_prod.empty:
        st.info("Nenhum produto cadastrado.")
    else:
        tipo_operacao = st.radio("Selecione a Ação:", ["📤 Retirada (Saída)", "📥 Devolução (Entrada)"], horizontal=True)
        tipo_mov_banco = "SAÍDA" if "Retirada" in tipo_operacao else "DEVOLUÇÃO"
        
        prod_selecionado = st.selectbox("Escolha o Item na Lista:", df_prod['nome'].tolist())
        row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
        
        col1, col2 = st.columns(2)
        with col1:
            st.info(f"📦 Item: {row['nome']} | Saldo: {row['quantidade']}")
            qtd_mov_final = st.number_input("Quantidade:", min_value=0.1, value=1.0)
            
        with col2:
            if tipo_mov_banco == "SAÍDA":
                quem_retirou = st.text_input("👤 Nome de Quem Retirou *")
                projeto_destino = st.selectbox("🏗️ Projeto Destino *", lista_projetos)
            else:
                quem_retirou = st.text_input("👤 Devolvido por:", value="-")
                projeto_destino = "-"

        if st.button("Confirmar Movimentação", type="primary"):
            if tipo_mov_banco == "SAÍDA" and not quem_retirou.strip():
                st.error("❌ Por favor, informe o nome de quem está retirando!")
            else:
                ok, msg = movimentar_produto(
                    int(row['id']), tipo_mov_banco, float(qtd_mov_final), float(row['quantidade']),
                    st.session_state.usuario, "", quem_retirou, projeto_destino
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

elif "Dashboard Analytics" in opcao:
    st.title("📊 BI Dashboard - Indicadores por Projeto")
    df_hist = buscar_historico()
    
    if not df_hist.empty:
        df_hist['data_hora'] = pd.to_datetime(df_hist['data_hora'])
        data_limite_7d = datetime.now() - timedelta(days=7)
        df_7d = df_hist[(df_hist['tipo'] == 'SAÍDA') & (df_hist['data_hora'] >= data_limite_7d)]
        
        st.markdown("##### 📅 Movimentações de Projetos (Últimos 7 Dias)")
        if not df_7d.empty:
            proj_7d = df_7d.groupby('projeto_destino')['quantidade'].sum().reset_index()
            st.bar_chart(proj_7d.set_index('projeto_destino'))
            st.dataframe(df_7d[['data_hora', 'produto', 'quantidade', 'quem_retirou', 'projeto_destino']], use_container_width=True)
        else:
            st.info("Nenhuma retirada vinculada a projetos nos últimos 7 dias.")

elif "Histórico / Auditoria" in opcao:
    st.title("📜 Histórico e Auditoria")
    st.dataframe(buscar_historico(), use_container_width=True)

elif "Fox Assistente" in opcao:
    st.title("🦊 Fox Assistente")
    if not client_gemini:
        st.error("Chave de API do Gemini não configurada.")
    else:
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []
            
        for msg in st.session_state.chat_history:
            avatar_icon = "🦊" if msg["role"] == "assistant" else "👤"
            with st.chat_message(msg["role"], avatar=avatar_icon):
                st.markdown(msg["content"])
                
        if prompt := st.chat_input("Pergunte algo para a Fox Assistente:"):
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
                
            try:
                contexto_banco = gerar_contexto_dados_sistema()
                instrucao = f"{contexto_banco}\n\nVocê é a Fox Assistente 🦊. Responda à dúvida do usuário:\n{prompt}"
                
                response = client_gemini.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=instrucao,
                )
                st.session_state.chat_history.append({"role": "assistant", "content": response.text})
                with st.chat_message("assistant", avatar="🦊"):
                    st.markdown(response.text)
            except Exception as e:
                st.error(f"Erro ao conversar com a Fox Assistente: {e}")
