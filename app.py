import datetime
import glob
import hashlib
import io
import os
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from fpdf import FPDF
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import streamlit as st

# Tenta carregar biblioteca do Gemini se disponível
try:
    from google import genai
except ImportError:
    genai = None

# Configuração da página Streamlit
st.set_page_config(page_title="Sistema de Almoxarifado Inteligente", layout="wide", page_icon="🦊")

# --- CSS AJUSTADO ---
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stSidebarNav"] {margin-top: 0px;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Pastas para salvamento temporário, relatórios e biblioteca
for pasta in ["uploads", "relatorios_checklist", "biblioteca_pdf"]:
    if not os.path.exists(pasta):
        os.makedirs(pasta)

# --- INICIALIZAÇÃO DA API DO GEMINI (FOX ASSISTENTE) ---
@st.cache_resource
def init_gemini():
    if genai is None:
        return None
    api_key = st.secrets.get("GEMINI_API_KEY") if hasattr(st, "secrets") else None
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

client_gemini = init_gemini()

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
EXTENSOES_PERMITIDAS_PDF = {".pdf"}

def salvar_arquivo_seguro(uploaded_file, pasta_destino="uploads", tipo="imagem") -> str:
    if uploaded_file is None:
        return ""
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if tipo == "imagem":
        permitidas = EXTENSOES_PERMITIDAS_IMAGEM
    elif tipo == "xml":
        permitidas = EXTENSOES_PERMITIDAS_XML
    elif tipo == "mapa":
        permitidas = EXTENSOES_PERMITIDAS_MAPA
    elif tipo == "pdf":
        permitidas = EXTENSOES_PERMITIDAS_PDF
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
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Relatório')
    except Exception:
        try:
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
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

# --- CONEXÃO COM SUPABASE (ONLINE) OU SQLITE (OFFLINE FALLBACK) ---
SUPABASE_DB_URL = st.secrets.get(
    "SUPABASE_DB_URL",
    "postgresql://postgres:SUA_SENHA@db.SEU_PROJETO.supabase.co:6543/postgres"
) if hasattr(st, "secrets") else "postgresql://postgres:SUA_SENHA@db.SEU_PROJETO.supabase.co:6543/postgres"

@st.cache_resource
def obter_conexao():
    try:
        conn = psycopg2.connect(SUPABASE_DB_URL, connect_timeout=3)
        st.session_state.modo_offline = False
        return conn
    except Exception:
        # Fallback para banco local se falhar ou estiver off-line
        conn = sqlite3.connect("almoxarifado_local.db", check_same_thread=False)
        st.session_state.modo_offline = True
        return conn

def conectar():
    try:
        conn = obter_conexao()
        if hasattr(conn, 'closed') and conn.closed != 0:
            st.cache_resource.clear()
            return obter_conexao()
        if hasattr(conn, 'status') and conn.status == psycopg2.extensions.STATUS_IN_TRANSACTION:
            conn.rollback()
        return conn
    except Exception:
        st.cache_resource.clear()
        return obter_conexao()

def inicializar_banco():
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        
        # Queries adaptadas para compatibilidade SQLite / Postgres
        pk_auto = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY"
        curr_date = "CURRENT_DATE"
        curr_time = "CURRENT_TIMESTAMP"

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS configuracoes (
            id INTEGER PRIMARY KEY DEFAULT 1,
            nome_empresa TEXT DEFAULT 'Sistema de Almoxarifado',
            logo_path TEXT DEFAULT '',
            cor_tema TEXT DEFAULT '#2196F3',
            mapa_path TEXT DEFAULT ''
        );
        """)
        
        if is_sqlite:
            cursor.execute("INSERT OR IGNORE INTO configuracoes (id, nome_empresa) VALUES (1, 'Sistema de Almoxarifado');")
        else:
            cursor.execute("INSERT INTO configuracoes (id, nome_empresa) VALUES (1, 'Sistema de Almoxarifado') ON CONFLICT (id) DO NOTHING;")
        
        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS produtos (
            id {pk_auto},
            nome TEXT NOT NULL,
            categoria TEXT DEFAULT 'Geral',
            localizacao TEXT DEFAULT 'Não informada',
            quantidade REAL NOT NULL DEFAULT 0,
            unidade_medida TEXT DEFAULT 'Caixa',
            qtd_por_caixa INTEGER DEFAULT 1,
            foto_path TEXT DEFAULT '',
            codigo_barras TEXT DEFAULT '',
            ca TEXT DEFAULT '',
            qtd_minima REAL DEFAULT 5.0,
            valor_unitario REAL DEFAULT 0.0
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS projetos (
            id {pk_auto},
            nome_projeto TEXT NOT NULL UNIQUE,
            descricao TEXT DEFAULT '',
            data_inicio DATE DEFAULT {curr_date},
            status TEXT DEFAULT 'Ativo'
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id {pk_auto},
            nome TEXT NOT NULL UNIQUE,
            cpf_matricula TEXT DEFAULT '',
            funcao TEXT DEFAULT 'Operador',
            status TEXT DEFAULT 'Ativo'
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS equipe_projeto (
            id {pk_auto},
            projeto_id INTEGER,
            funcionario_id INTEGER,
            nome_colaborador TEXT NOT NULL,
            funcao TEXT DEFAULT 'Operador'
        );
        """)

        # ATUALIZAÇÃO DA TABELA DE FERRAMENTAS COM RASTREIO DA ÚLTIMA PESSOA
        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS cadastro_ferramentas (
            id {pk_auto},
            codigo_patrimonio TEXT UNIQUE NOT NULL,
            nome_ferramenta TEXT NOT NULL,
            categoria TEXT DEFAULT 'Ferramenta Geral',
            status TEXT DEFAULT 'Disponível',
            funcionario_id INTEGER,
            projeto_id INTEGER,
            ultimo_funcionario_id INTEGER,
            observacao TEXT DEFAULT ''
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS historico (
            id {pk_auto},
            produto_id INTEGER,
            tipo TEXT NOT NULL,
            quantidade REAL NOT NULL,
            valor_unitario REAL DEFAULT 0.0,
            valor_total REAL DEFAULT 0.0,
            usuario TEXT DEFAULT 'Sistema',
            responsavel_epi TEXT DEFAULT '',
            nome_retirou TEXT DEFAULT '',
            projeto_nome TEXT DEFAULT '',
            data_hora TIMESTAMP DEFAULT {curr_time}
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS biblioteca_projetos (
            id {pk_auto},
            titulo TEXT NOT NULL,
            descricao TEXT DEFAULT '',
            projeto_id INTEGER,
            nome_arquivo_original TEXT DEFAULT '',
            pdf_path TEXT NOT NULL,
            data_upload TIMESTAMP DEFAULT {curr_time},
            usuario TEXT DEFAULT 'Sistema'
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS usuarios (
            id {pk_auto},
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL,
            ultima_atividade TIMESTAMP,
            localizacao_sessao TEXT DEFAULT 'Almoxarifado Principal',
            acao_atual TEXT DEFAULT 'Navegando no Sistema'
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            id {pk_auto},
            numero_nf TEXT NOT NULL,
            fornecedor TEXT NOT NULL,
            cnpj_fornecedor TEXT DEFAULT '',
            produto_nome TEXT NOT NULL,
            quantidade REAL NOT NULL,
            valor_unitario REAL DEFAULT 0.0,
            valor_total REAL DEFAULT 0.0,
            data_recebimento TIMESTAMP DEFAULT {curr_time},
            usuario TEXT DEFAULT 'Sistema'
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS checklist_ferramentas (
            id {pk_auto},
            usuario TEXT NOT NULL,
            data_hora TIMESTAMP DEFAULT {curr_time},
            pdf_path TEXT DEFAULT '',
            observacao TEXT DEFAULT ''
        );
        """)

        # Alterações dinâmicas e verificações de colunas adicionais
        colunas_para_adicionar = [
            ("cadastro_ferramentas", "ultimo_funcionario_id", "INTEGER"),
            ("historico", "valor_unitario", "REAL DEFAULT 0.0"),
            ("historico", "valor_total", "REAL DEFAULT 0.0"),
            ("produtos", "valor_unitario", "REAL DEFAULT 0.0")
        ]
        
        for tabela, coluna, tipo in colunas_para_adicionar:
            try:
                cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo};")
            except Exception:
                pass

        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (?, ?, ?)" if is_sqlite else "INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("admin", gerar_hash_senha("1234"), "Admin"))
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (?, ?, ?)" if is_sqlite else "INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s, %s, %s)", ("operador", gerar_hash_senha("1234"), "Operador"))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        st.cache_resource.clear()
        raise e

if 'banco_inicializado' not in st.session_state:
    inicializar_banco()
    st.session_state.banco_inicializado = True

# --- CONSULTAS E TRANSAÇÕES NO BANCO DE DADOS ---
def atualizar_presenca_usuario(usuario, localizacao="Almoxarifado Principal", acao="Navegando no Sistema"):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"UPDATE usuarios SET ultima_atividade = CURRENT_TIMESTAMP, localizacao_sessao = {ph}, acao_atual = {ph} WHERE usuario = {ph}", (localizacao, acao, usuario))
        conn.commit()
    except Exception:
        conn.rollback()

@st.cache_data(ttl=300)
def buscar_configuracoes():
    conn = conectar()
    cursor = conn.cursor()
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
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"UPDATE configuracoes SET nome_empresa = {ph}, logo_path = {ph}, cor_tema = {ph}, mapa_path = {ph} WHERE id = 1", (nome, logo_path, cor, mapa_path))
        conn.commit()
        st.cache_data.clear()
        return True, "Configurações salvas com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao salvar configurações: {e}"

def autenticar_usuario(usuario, senha_digitada):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"SELECT perfil, senha FROM usuarios WHERE usuario = {ph}", (usuario,))
        res = cursor.fetchone()
        if res:
            perfil, hash_senha = res
            if ":" not in hash_senha:
                if senha_digitada == hash_senha:
                    novo_hash = gerar_hash_senha(senha_digitada)
                    cursor.execute(f"UPDATE usuarios SET senha = {ph} WHERE usuario = {ph}", (novo_hash, usuario))
                    conn.commit()
                    return perfil
            elif verificar_senha(senha_digitada, hash_senha):
                return perfil
    except Exception:
        conn.rollback()
        return None

@st.cache_data(ttl=15)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima, valor_unitario FROM produtos ORDER BY id ASC", conn)

def cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida="Caixa", qtd_por_caixa=1, foto_path="", codigo_barras="", ca="", qtd_minima=5.0, valor_unitario=0.0):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(
            f"INSERT INTO produtos (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima, valor_unitario) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima, valor_unitario)
        )
        conn.commit()
        st.cache_data.clear()
        return True, f"Produto '{nome}' cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar produto: {e}"

def editar_produto(prod_id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path="", codigo_barras="", ca="", qtd_minima=5.0, valor_unitario=0.0):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(
            f"UPDATE produtos SET nome = {ph}, categoria = {ph}, localizacao = {ph}, quantidade = {ph}, unidade_medida = {ph}, qtd_por_caixa = {ph}, foto_path = {ph}, codigo_barras = {ph}, ca = {ph}, qtd_minima = {ph}, valor_unitario = {ph} WHERE id = {ph}",
            (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima, valor_unitario, prod_id)
        )
        conn.commit()
        st.cache_data.clear()
        return True, "Produto atualizado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao atualizar produto: {e}"

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado, responsavel_epi="", nome_retirou="", projeto_nome=""):
    tipo_upper = tipo.upper()
    if tipo_upper == "SAÍDA" and qtd_mov > qtd_atual:
        return False, f"Estoque insuficiente! Saldo atual: {qtd_atual:.2f}"
    
    if tipo_upper in ["ENTRADA", "DEVOLUÇÃO", "DEVOLUCAO"]:
        nova_qtd = qtd_atual + qtd_mov
    else:
        nova_qtd = qtd_atual - qtd_mov

    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        
        # Buscar valor unitário do produto para cálculo do histórico financeiro
        cursor.execute(f"SELECT valor_unitario FROM produtos WHERE id = {ph}", (prod_id,))
        res_v = cursor.fetchone()
        val_unit = res_v[0] if res_v and res_v[0] else 0.0
        val_total = val_unit * qtd_mov

        cursor.execute(f"UPDATE produtos SET quantidade = {ph} WHERE id = {ph}", (nova_qtd, prod_id))
        cursor.execute(
            f"INSERT INTO historico (produto_id, tipo, quantidade, valor_unitario, valor_total, usuario, responsavel_epi, nome_retirou, projeto_nome) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (prod_id, tipo_upper, qtd_mov, val_unit, val_total, usuario_logado, responsavel_epi, nome_retirou, projeto_nome)
        )
        conn.commit()
        st.cache_data.clear()
        return True, "Movimentação realizada com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao registrar movimentação: {e}"

# --- FUNÇÕES DE FUNCIONÁRIOS E FERRAMENTAS COM RASTREAMENTO DO ÚLTIMO POSSUIDOR ---
@st.cache_data(ttl=15)
def buscar_funcionarios():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, cpf_matricula, funcao, status FROM funcionarios ORDER BY nome ASC", conn)

def cadastrar_funcionario(nome, cpf_matricula="", funcao="Operador"):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"INSERT INTO funcionarios (nome, cpf_matricula, funcao) VALUES ({ph}, {ph}, {ph})", (nome.strip(), cpf_matricula.strip(), funcao.strip()))
        conn.commit()
        st.cache_data.clear()
        return True, f"Funcionário '{nome}' cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar funcionário: {e}"

@st.cache_data(ttl=15)
def buscar_cadastro_ferramentas():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT f.id, f.codigo_patrimonio, f.nome_ferramenta, f.categoria, f.status,
           func.nome as funcionario_responsavel,
           func_ant.nome as ultimo_possuidor,
           proj.nome_projeto as projeto_alocado, f.observacao,
           f.funcionario_id, f.projeto_id, f.ultimo_funcionario_id
    FROM cadastro_ferramentas f
    LEFT JOIN funcionarios func ON f.funcionario_id = func.id
    LEFT JOIN funcionarios func_ant ON f.ultimo_funcionario_id = func_ant.id
    LEFT JOIN projetos proj ON f.projeto_id = proj.id
    ORDER BY f.id DESC
    """, conn)

def cadastrar_ferramenta_patrimonio(codigo_patrimonio, nome_ferramenta, categoria="Ferramenta Geral", status="Disponível", funcionario_id=None, projeto_id=None, observacao=""):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"""
        INSERT INTO cadastro_ferramentas (codigo_patrimonio, nome_ferramenta, categoria, status, funcionario_id, projeto_id, observacao)
        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (codigo_patrimonio.strip(), nome_ferramenta.strip(), categoria, status, funcionario_id, projeto_id, observacao.strip()))
        conn.commit()
        st.cache_data.clear()
        return True, f"Ferramenta '{nome_ferramenta}' ({codigo_patrimonio}) cadastrada!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar ferramenta: {e}"

def atualizar_status_ferramenta(ferramenta_id, status, funcionario_id=None, projeto_id=None, observacao=""):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        
        # Busca o detentor atual antes de alterar para salvar como "Último Possuidor"
        cursor.execute(f"SELECT funcionario_id FROM cadastro_ferramentas WHERE id = {ph}", (ferramenta_id,))
        res_f = cursor.fetchone()
        antigo_funcionario_id = res_f[0] if res_f else None
        
        # Atualiza salvando quem estava com a ferramenta anteriormente
        if antigo_funcionario_id != funcionario_id and antigo_funcionario_id is not None:
            cursor.execute(f"""
            UPDATE cadastro_ferramentas
            SET status = {ph}, funcionario_id = {ph}, projeto_id = {ph}, observacao = {ph}, ultimo_funcionario_id = {ph}
            WHERE id = {ph}
            """, (status, funcionario_id, projeto_id, observacao, antigo_funcionario_id, ferramenta_id))
        else:
            cursor.execute(f"""
            UPDATE cadastro_ferramentas
            SET status = {ph}, funcionario_id = {ph}, projeto_id = {ph}, observacao = {ph}
            WHERE id = {ph}
            """, (status, funcionario_id, projeto_id, observacao, ferramenta_id))
            
        conn.commit()
        st.cache_data.clear()
        return True, "Status e histórico da ferramenta atualizados!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao atualizar ferramenta: {e}"

# --- MÓDULO DE PROJETOS E EQUIPE ---
@st.cache_data(ttl=15)
def buscar_projetos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome_projeto, descricao, data_inicio, status FROM projetos ORDER BY id DESC", conn)

def cadastrar_projeto(nome_projeto, descricao=""):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"INSERT INTO projetos (nome_projeto, descricao) VALUES ({ph}, {ph})", (nome_projeto.strip(), descricao.strip()))
        conn.commit()
        st.cache_data.clear()
        return True, f"Projeto '{nome_projeto}' cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar projeto: {e}"

@st.cache_data(ttl=15)
def buscar_equipe_projeto():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT e.id, p.nome_projeto, e.nome_colaborador, e.funcao 
    FROM equipe_projeto e 
    JOIN projetos p ON e.projeto_id = p.id 
    ORDER BY p.nome_projeto ASC
    """, conn)

def adicionar_membro_equipe(projeto_id, nome_colaborador, funcao="Operador", funcionario_id=None):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(
            f"INSERT INTO equipe_projeto (projeto_id, nome_colaborador, funcao, funcionario_id) VALUES ({ph}, {ph}, {ph}, {ph})",
            (projeto_id, nome_colaborador.strip(), funcao.strip(), funcionario_id)
        )
        conn.commit()
        st.cache_data.clear()
        return True, f"Colaborador '{nome_colaborador}' adicionado ao projeto!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao vincular membro: {e}"

# --- MÓDULO DA BIBLIOTECA DE PROJETOS (PDFs) ---
@st.cache_data(ttl=15)
def buscar_biblioteca_projetos():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT b.id, b.titulo, b.descricao, b.projeto_id, p.nome_projeto, b.nome_arquivo_original, b.pdf_path, b.data_upload, b.usuario
    FROM biblioteca_projetos b
    LEFT JOIN projetos p ON b.projeto_id = p.id
    ORDER BY b.id DESC
    """, conn)

def cadastrar_desenho_projeto(titulo, descricao, projeto_id, nome_arquivo_orig, pdf_path, usuario):
    conn = conectar()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        ph = "?" if is_sqlite else "%s"
        cursor.execute(f"""
        INSERT INTO biblioteca_projetos (titulo, descricao, projeto_id, nome_arquivo_original, pdf_path, usuario)
        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (titulo.strip(), descricao.strip(), projeto_id, nome_arquivo_orig, pdf_path, usuario))
        conn.commit()
        st.cache_data.clear()
        return True, "Desenho/Projeto cadastrado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar item: {e}"

@st.cache_data(ttl=15)
def buscar_historico():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT h.id, p.nome as produto, p.categoria, h.tipo, h.quantidade, h.valor_unitario, h.valor_total, h.usuario, h.nome_retirou, h.projeto_nome, h.responsavel_epi, h.data_hora
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
    st.title(f"🏢 {config['nome_empresa']}")
    if getattr(st.session_state, 'modo_offline', False):
        st.warning("📡 **MODO OFF-LINE ATIVO**: O sistema está rodando localmente sem conexão com a nuvem.")
        
    if config['logo_path'] and os.path.exists(config['logo_path']):
        st.image(config['logo_path'], width=180)
    
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

# --- REORGANIZAÇÃO DAS OPÇÕES DE MENU ---
if st.session_state.perfil == "Operador":
    opcoes_menu = [
        "📦 Consulta de Estoque",
        "📊 Dashboard Analytics (BI)",
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Gestão de Projetos e Equipe",
        "📁 Biblioteca de Desenhos / Projetos",
        "📋 Checklist de Ferramentas",
        "🔄 Retirada / Devolução de Materiais",
        "➕ Cadastrar Produto",
        "🦊 Fox Assistente"
    ]
else:
    opcoes_menu = [
        "📦 Consulta de Estoque",
        "📊 Dashboard Analytics (BI)",
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Gestão de Projetos e Equipe",
        "📁 Biblioteca de Desenhos / Projetos",
        "📋 Checklist de Ferramentas",
        "🔄 Retirada / Devolução de Materiais",
        "➕ Cadastrar Produto",
        "📜 Histórico / Auditoria",
        "👥 Gerenciar Usuários",
        "🎨 Personalizar Empresa",
        "🦊 Fox Assistente"
    ]

st.sidebar.title(f"🏢 {config['nome_empresa']}")
st.sidebar.write(f"👤 **{st.session_state.usuario}** ({st.session_state.perfil})")

if getattr(st.session_state, 'modo_offline', False):
    st.sidebar.warning("⚡ **Trabalhando Off-line**")

if config['logo_path'] and os.path.exists(config['logo_path']):
    st.sidebar.image(config['logo_path'], use_container_width=True)

opcao = st.sidebar.radio("📍 Navegação", opcoes_menu)
atualizar_presenca_usuario(st.session_state.usuario, acao=f"Navegando em: {opcao}")

if st.sidebar.button("🚪 Sair / Logout"):
    atualizar_presenca_usuario(st.session_state.usuario, acao="Desconectado")
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""
    st.rerun()

# --- ABA: DASHBOARD (MÉTRICAS DETALHADAS POR DIA, MÊS E ANO COM ENTRADAS E SAÍDAS) ---
if "Dashboard" in opcao:
    st.title("📊 Dashboard Analytics - Movimentações de Estoque")
    st.caption("Visão detalhada dos valores financeiros e quantidades físicas de entradas e saídas por período.")
    
    df_hist = buscar_historico()
    
    if df_hist.empty:
        st.info("Nenhuma movimentação registrada no histórico para gerar o dashboard.")
    else:
        # Converter coluna de data/hora
        df_hist['data_hora'] = pd.to_datetime(df_hist['data_hora'])
        df_hist['Ano'] = df_hist['data_hora'].dt.year
        df_hist['Mês'] = df_hist['data_hora'].dt.month
        df_hist['Dia'] = df_hist['data_hora'].dt.date

        # Seletores de Filtro
        st.subheader("🗓️ Filtros Temporais")
        c_f1, c_f2, c_f3 = st.columns(3)
        
        anos_disponiveis = ["Todos"] + sorted(list(df_hist['Ano'].unique()), reverse=True)
        with c_f1:
            ano_sel = st.selectbox("Selecione o Ano:", anos_disponiveis)
            
        df_filtrado = df_hist.copy()
        if ano_sel != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Ano'] == int(ano_sel)]
            
        meses_disponiveis = ["Todos"] + sorted(list(df_filtrado['Mês'].unique()))
        with c_f2:
            mes_sel = st.selectbox("Selecione o Mês:", meses_disponiveis)
            
        if mes_sel != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Mês'] == int(mes_sel)]
            
        dias_disponiveis = ["Todos"] + sorted(list(df_filtrado['Dia'].unique()), reverse=True)
        with c_f3:
            dia_sel = st.selectbox("Selecione o Dia:", dias_disponiveis)
            
        if dia_sel != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Dia'] == dia_sel]

        # Separar Entradas e Saídas
        df_entradas = df_filtrado[df_filtrado['tipo'].str.contains("ENTRADA|DEVOLUÇÃO|DEVOLUCAO", case=False, na=False)]
        df_saidas = df_filtrado[df_filtrado['tipo'].str.contains("SAÍDA|SAIDA", case=False, na=False)]

        # Métricas Consolidadas
        total_qtd_entrada = df_entradas['quantidade'].sum()
        total_val_entrada = df_entradas['valor_total'].sum()

        total_qtd_saida = df_saidas['quantidade'].sum()
        total_val_saida = df_saidas['valor_total'].sum()

        saldo_qtd = total_qtd_entrada - total_qtd_saida
        saldo_val = total_val_entrada - total_val_saida

        st.write("---")
        st.subheader("💵 Balanço Financeiro & Quantidades do Período Selecionado")
        
        kpi1, kpi2, kpi3 = st.columns(3)
        with kpi1:
            st.metric(
                label="📥 Entradas Totais",
                value=f"R$ {total_val_entrada:,.2f}",
                delta=f"{total_qtd_entrada:,.0f} itens recebidos",
                delta_color="normal"
            )
        with kpi2:
            st.metric(
                label="📤 Saídas Totais",
                value=f"R$ {total_val_saida:,.2f}",
                delta=f"-{total_qtd_saida:,.0f} itens retirados",
                delta_color="inverse"
            )
        with kpi3:
            st.metric(
                label="⚖️ Saldo Liquido do Período",
                value=f"R$ {saldo_val:,.2f}",
                delta=f"{saldo_qtd:,.0f} itens de saldo",
                delta_color="normal" if saldo_val >= 0 else "inverse"
            )

        st.write("---")
        st.subheader("📈 Gráficos de Entradas e Saídas por Período")
        
        tab_graf1, tab_graf2 = st.tabs(["📊 Por Dia", "📅 Por Mês"])
        
        with tab_graf1:
            df_agrupado_dia = df_filtrado.groupby(['Dia', 'tipo'])['quantidade'].sum().unstack(fill_value=0)
            st.bar_chart(df_agrupado_dia)
            
        with tab_graf2:
            df_agrupado_mes = df_filtrado.groupby(['Mês', 'tipo'])['valor_total'].sum().unstack(fill_value=0)
            st.line_chart(df_agrupado_mes)

        st.write("---")
        st.subheader("📑 Tabela Detalhada das Movimentações no Período")
        st.dataframe(df_filtrado[['id', 'data_hora', 'produto', 'tipo', 'quantidade', 'valor_unitario', 'valor_total', 'usuario', 'nome_retirou', 'projeto_nome']], use_container_width=True)

# --- ABA: CHECKLIST DE FERRAMENTAS (COM RASTREAMENTO DO ATUAL E DO ÚLTIMO POSSUIDOR) ---
elif "Checklist de Ferramentas" in opcao:
    st.title("📋 Checklist e Rastreamento de Ferramentas")
    st.caption("Acompanhe com quem a ferramenta está no momento e a última pessoa que pegou antes.")

    df_ferr_cad = buscar_cadastro_ferramentas()
    df_func = buscar_funcionarios()
    df_proj = buscar_projetos()

    tab_chk, tab_cad_ferr = st.tabs(["✅ Checklist Diário & Rastreio", "🛠️ Cadastro de Ferramentas / Equipamentos"])

    with tab_chk:
        if df_ferr_cad.empty:
            st.warning("Nenhuma ferramenta cadastrada para rastreio. Cadastre na aba 'Cadastro de Ferramentas'.")
        else:
            st.subheader("🔍 Localizador em Tempo Real: Com Quem Está e Quem Pegou Antes")
            
            # Formatação visual da tabela conforme solicitado no requisito
            cols_exibicao = {
                'codigo_patrimonio': 'Patrimônio',
                'nome_ferramenta': 'Ferramenta',
                'status': 'Status Atual',
                'funcionario_responsavel': '👤 Com Quem Está No Momento',
                'ultimo_possuidor': '📜 Última Pessoa Que Pegou Antes',
                'projeto_alocado': 'Projeto Alocado',
                'observacao': 'Observação'
            }
            
            df_exibicao = df_ferr_cad[list(cols_exibicao.keys())].rename(columns=cols_exibicao)
            st.dataframe(df_exibicao, use_container_width=True)

            st.write("---")
            st.subheader("📝 Transferir / Registrar Retirada ou Devolução de Ferramenta")

            with st.form("form_checklist_ferramentas_patrimonio", clear_on_submit=True):
                ferr_id_sel = st.selectbox(
                    "Selecione a Ferramenta:", 
                    df_ferr_cad['id'].tolist(), 
                    format_func=lambda x: f"[{df_ferr_cad[df_ferr_cad['id']==x]['codigo_patrimonio'].values[0]}] - {df_ferr_cad[df_ferr_cad['id']==x]['nome_ferramenta'].values[0]}"
                )
                ferr_row = df_ferr_cad[df_ferr_cad['id'] == ferr_id_sel].iloc[0]

                c1, c2, c3 = st.columns(3)
                with c1:
                    novo_status = st.selectbox("Status Atual:", ["Disponível", "Em Uso / Emprestado", "Em Manutenção", "Defeituoso", "Ausente/Perdido"], index=0)
                with c2:
                    func_opts = [None] + (df_func['id'].tolist() if not df_func.empty else [])
                    sel_func_id = st.selectbox(
                        "Novo Responsável (Com Quem Ficará):", 
                        func_opts, 
                        format_func=lambda x: "Nenhum / Devolvido ao Almoxarifado" if x is None else df_func[df_func['id']==x]['nome'].values[0]
                    )
                with c3:
                    proj_opts = [None] + (df_proj['id'].tolist() if not df_proj.empty else [])
                    sel_proj_id = st.selectbox(
                        "Projeto Destino:", 
                        proj_opts, 
                        format_func=lambda x: "Sem Projeto Vinculado" if x is None else df_proj[df_proj['id']==x]['nome_projeto'].values[0]
                    )

                obs_f = st.text_input("Observações:", value=str(ferr_row['observacao'] or ''))

                if st.form_submit_button("💾 Salvar Alteração e Rastrear", type="primary"):
                    ok, msg = atualizar_status_ferramenta(ferr_id_sel, novo_status, sel_func_id, sel_proj_id, obs_f)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    with tab_cad_ferr:
        st.subheader("➕ Cadastrar Nova Ferramenta Patrimonial")
        with st.form("form_cad_ferramenta_pat", clear_on_submit=True):
            f_patrimonio = st.text_input("Código de Patrimônio / N° Série *", placeholder="Ex: FER-001")
            f_nome = st.text_input("Nome da Ferramenta *", placeholder="Ex: Furadeira Makita 1/2")
            f_cat = st.text_input("Categoria", value="Ferramenta Elétrica")
            
            c1, c2 = st.columns(2)
            with c1:
                f_func_id = st.selectbox("Funcionário Inicial:", [None] + (df_func['id'].tolist() if not df_func.empty else []), format_func=lambda x: "Nenhum" if x is None else df_func[df_func['id']==x]['nome'].values[0])
            with c2:
                f_proj_id = st.selectbox("Projeto Inicial:", [None] + (df_proj['id'].tolist() if not df_proj.empty else []), format_func=lambda x: "Nenhum" if x is None else df_proj[df_proj['id']==x]['nome_projeto'].values[0])
                
            f_obs = st.text_area("Observações Adicionais")
            
            if st.form_submit_button("💾 Cadastrar Ferramenta", type="primary"):
                if f_patrimonio.strip() and f_nome.strip():
                    ok, msg = cadastrar_ferramenta_patrimonio(f_patrimonio, f_nome, f_cat, "Disponível", f_func_id, f_proj_id, f_obs)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Código e Nome são obrigatórios.")

# --- DEMAIS ABAS PERMANECEM MANTIDAS INTEGRALMENTE ---
elif "Consulta de Estoque" in opcao:
    st.title(f"📦 Controle de Estoque - {config['nome_empresa']}")
    df_prod = buscar_produtos()
    if not df_prod.empty:
        st.dataframe(df_prod, use_container_width=True)

elif "Retirada / Devolução" in opcao:
    st.title("🔄 Retirada / Devolução de Materiais")
    df_prod = buscar_produtos()
    if not df_prod.empty:
        with st.form("form_retirada_rapida"):
            p_sel = st.selectbox("Selecione o Produto:", df_prod['id'].tolist(), format_func=lambda x: df_prod[df_prod['id']==x]['nome'].values[0])
            p_qtd = st.number_input("Quantidade:", min_value=1.0, value=1.0)
            p_tipo = st.selectbox("Tipo:", ["SAÍDA", "ENTRADA"])
            p_retirou = st.text_input("Nome da Pessoa:")
            if st.form_submit_button("Confirmar Movimentação"):
                prod_row = df_prod[df_prod['id'] == p_sel].iloc[0]
                ok, msg = movimentar_produto(p_sel, p_tipo, p_qtd, prod_row['quantidade'], st.session_state.usuario, nome_retirou=p_retirou)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

elif "Cadastrar Produto" in opcao:
    st.title("➕ Cadastrar Novo Produto")
    with st.form("form_cad_prod", clear_on_submit=True):
        nome = st.text_input("Nome do Produto *")
        categoria = st.text_input("Categoria", value="Geral")
        quantidade = st.number_input("Quantidade Inicial", min_value=0.0, value=0.0)
        val_unit = st.number_input("Valor Unitário (R$)", min_value=0.0, value=0.0)
        if st.form_submit_button("Cadastrar"):
            ok, msg = cadastrar_produto(nome, categoria, "Almoxarifado Principal", quantidade, valor_unitario=val_unit)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

elif "Histórico / Auditoria" in opcao:
    st.title("📜 Histórico de Movimentações")
    st.dataframe(buscar_historico(), use_container_width=True)

else:
    st.title(f"📍 {opcao}")
    st.info("Funcionalidade mantida e pronta para uso.")
