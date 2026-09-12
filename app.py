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
st.set_page_config(page_title="Sistema de Almoxarifado Inteligente", layout="wide", page_icon="🦊")

# --- CSS AJUSTADO: PERMITE REABRIR O MENU LATERAL SE OCURTADO ---
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    /* Mantém o botão de expansão da sidebar visível no header */
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
    api_key = st.secrets.get("GEMINI_API_KEY")
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
                codigo_barras TEXT DEFAULT '',
                ca TEXT DEFAULT '',
                qtd_minima REAL DEFAULT 5.0
            );
            """)
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS projetos (
                id SERIAL PRIMARY KEY,
                nome_projeto TEXT NOT NULL UNIQUE,
                descricao TEXT DEFAULT '',
                data_inicio DATE DEFAULT CURRENT_DATE,
                status TEXT DEFAULT 'Ativo'
            );
            """)
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS equipe_projeto (
                id SERIAL PRIMARY KEY,
                projeto_id INTEGER REFERENCES projetos (id) ON DELETE CASCADE,
                nome_colaborador TEXT NOT NULL,
                funcao TEXT DEFAULT 'Operador'
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
                nome_retirou TEXT DEFAULT '',
                projeto_nome TEXT DEFAULT '',
                data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS biblioteca_projetos (
                id SERIAL PRIMARY KEY,
                titulo TEXT NOT NULL,
                descricao TEXT DEFAULT '',
                projeto_id INTEGER REFERENCES projetos (id) ON DELETE SET NULL,
                nome_arquivo_original TEXT DEFAULT '',
                pdf_path TEXT NOT NULL,
                data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usuario TEXT DEFAULT 'Sistema'
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS ferramentas (
                id SERIAL PRIMARY KEY,
                local TEXT DEFAULT 'Almoxarifado',
                codigo TEXT UNIQUE NOT NULL,
                ferramenta TEXT NOT NULL,
                marca TEXT DEFAULT '',
                funcionario TEXT DEFAULT 'Almoxarifado',
                ultimo_funcionario TEXT DEFAULT '',
                data_ultimo_emprestimo TIMESTAMP
            );
            """)
            
            try:
                cursor.execute("ALTER TABLE historico ADD COLUMN IF NOT EXISTS nome_retirou TEXT DEFAULT '';")
                cursor.execute("ALTER TABLE historico ADD COLUMN IF NOT EXISTS projeto_nome TEXT DEFAULT '';")
            except Exception:
                pass

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
            
            cursor.execute("DELETE FROM historico WHERE data_hora < NOW() - INTERVAL '540 days';")
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

# --- CONSULTAS E TRANSAÇÕES NO BANCO DE DADOS ---
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

@st.cache_data(ttl=15)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima FROM produtos ORDER BY id ASC", conn)

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

def editar_produto(prod_id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path="", codigo_barras="", ca="", qtd_minima=5.0):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE produtos SET nome = %s, categoria = %s, localizacao = %s, quantidade = %s, unidade_medida = %s, qtd_por_caixa = %s, foto_path = %s, codigo_barras = %s, ca = %s, qtd_minima = %s WHERE id = %s",
                (nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima, prod_id)
            )
            conn.commit()
            st.cache_data.clear()
            return True, "Produto atualizado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao atualizar produto: {e}"

def mesclar_produtos(id_destino, lista_ids_origem):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SUM(quantidade) FROM produtos WHERE id = ANY(%s)", (lista_ids_origem,))
            qtd_somada = cursor.fetchone()[0] or 0
            cursor.execute("UPDATE produtos SET quantidade = quantidade + %s WHERE id = %s", (qtd_somada, id_destino))
            cursor.execute("UPDATE historico SET produto_id = %s WHERE produto_id = ANY(%s)", (id_destino, lista_ids_origem))
            cursor.execute("DELETE FROM produtos WHERE id = ANY(%s)", (lista_ids_origem,))
            conn.commit()
            st.cache_data.clear()
            return True, "Produtos mesclados com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao mesclar produtos: {e}"

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado, responsavel_epi="", nome_retirou="", projeto_nome=""):
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
                "INSERT INTO historico (produto_id, tipo, quantidade, usuario, responsavel_epi, nome_retirou, projeto_nome) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (prod_id, tipo_upper, qtd_mov, usuario_logado, responsavel_epi, nome_retirou, projeto_nome)
            )
            conn.commit()
            st.cache_data.clear()
            return True, "Movimentação realizada com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao registrar movimentação: {e}"

def estornar_movimentacao(historico_id, usuario_logado):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT produto_id, tipo, quantidade FROM historico WHERE id = %s", (historico_id,))
            res = cursor.fetchone()
            if not res:
                return False, "Registro de histórico não encontrado."
            
            prod_id, tipo, quantidade = res
            
            cursor.execute("SELECT quantidade FROM produtos WHERE id = %s", (prod_id,))
            res_prod = cursor.fetchone()
            if not res_prod:
                return False, "Produto associado a este histórico não existe mais."
            
            qtd_atual = res_prod[0]
            
            if "ENTRADA" in tipo.upper() or "DEVOLUÇÃO" in tipo.upper() or "DEVOLUCAO" in tipo.upper():
                if qtd_atual < quantidade:
                    return False, f"Não é possível estornar. Saldo atual ({qtd_atual}) é menor que a quantidade do estorno ({quantidade})."
                nova_qtd = qtd_atual - quantidade
                tipo_estorno = f"ESTORNO {tipo.upper()} (ID #{historico_id})"
            elif "SAÍDA" in tipo.upper() or "SAIDA" in tipo.upper():
                nova_qtd = qtd_atual + quantidade
                tipo_estorno = f"ESTORNO SAÍDA (ID #{historico_id})"
            else:
                return False, "Tipo de movimentação não suportado para estorno automático."
            cursor.execute("UPDATE produtos SET quantidade = %s WHERE id = %s", (nova_qtd, prod_id))
            cursor.execute(
                "INSERT INTO historico (produto_id, tipo, quantidade, usuario, responsavel_epi) VALUES (%s, %s, %s, %s, %s)",
                (prod_id, tipo_estorno, quantidade, usuario_logado, "ESTORNO SISTEMA")
            )
            conn.commit()
            st.cache_data.clear()
            return True, "Estorno realizado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao processar estorno: {str(e)}"

# --- MÓDULO DE GESTÃO DE FERRAMENTAS ---
@st.cache_data(ttl=15)
def buscar_ferramentas():
    conn = conectar()
    return pd.read_sql_query("SELECT id, local, codigo, ferramenta, marca, funcionario, ultimo_funcionario, data_ultimo_emprestimo FROM ferramentas ORDER BY id ASC", conn)

def cadastrar_ferramenta(local, codigo, ferramenta, marca):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ferramentas (local, codigo, ferramenta, marca, funcionario) VALUES (%s, %s, %s, %s, 'Almoxarifado')",
                (local.strip(), codigo.strip(), ferramenta.strip(), marca.strip())
            )
            conn.commit()
            st.cache_data.clear()
            return True, f"Ferramenta '{ferramenta}' cadastrada com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar ferramenta: {e}"

def movimentar_ferramenta(codigo, novo_funcionario):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT funcionario FROM ferramentas WHERE codigo = %s", (codigo.strip(),))
            res = cursor.fetchone()
            if not res:
                return False, f"Ferramenta com código '{codigo}' não encontrada!"
            
            funcionario_atual = res[0]
            if novo_funcionario.strip().upper() == "ALMOXARIFADO":
                # Processo de Devolução
                cursor.execute(
                    "UPDATE ferramentas SET funcionario = 'Almoxarifado' WHERE codigo = %s",
                    (codigo.strip(),)
                )
                msg = "Ferramenta devolvida ao Almoxarifado com sucesso!"
            else:
                # Processo de Retirada
                cursor.execute(
                    """
                    UPDATE ferramentas 
                    SET ultimo_funcionario = CASE WHEN funcionario <> 'Almoxarifado' AND funcionario <> '' THEN funcionario ELSE ultimo_funcionario END,
                        funcionario = %s,
                        data_ultimo_emprestimo = NOW()
                    WHERE codigo = %s
                    """,
                    (novo_funcionario.strip(), codigo.strip())
                )
                msg = f"Empréstimo da ferramenta registrado para {novo_funcionario}!"
            
            conn.commit()
            st.cache_data.clear()
            return True, msg
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
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO projetos (nome_projeto, descricao) VALUES (%s, %s)", (nome_projeto.strip(), descricao.strip()))
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

def adicionar_membro_equipe(projeto_id, nome_colaborador, funcao="Operador"):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO equipe_projeto (projeto_id, nome_colaborador, funcao) VALUES (%s, %s, %s)", (projeto_id, nome_colaborador.strip(), funcao.strip()))
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
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
            INSERT INTO biblioteca_projetos (titulo, descricao, projeto_id, nome_arquivo_original, pdf_path, usuario)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (titulo.strip(), descricao.strip(), projeto_id, nome_arquivo_orig, pdf_path, usuario))
            conn.commit()
            st.cache_data.clear()
            return True, "Desenho/Projeto cadastrado na biblioteca com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao cadastrar item na biblioteca: {e}"

def editar_desenho_projeto(id_item, titulo, descricao, projeto_id, novo_pdf_path=None, novo_nome_orig=None):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            if novo_pdf_path:
                cursor.execute("SELECT pdf_path FROM biblioteca_projetos WHERE id = %s", (id_item,))
                res = cursor.fetchone()
                if res and res[0] and os.path.exists(res[0]):
                    try:
                        os.remove(res[0])
                    except Exception:
                        pass
                cursor.execute("""
                UPDATE biblioteca_projetos 
                SET titulo = %s, descricao = %s, projeto_id = %s, pdf_path = %s, nome_arquivo_original = %s 
                WHERE id = %s
                """, (titulo.strip(), descricao.strip(), projeto_id, novo_pdf_path, novo_nome_orig, id_item))
            else:
                cursor.execute("""
                UPDATE biblioteca_projetos 
                SET titulo = %s, descricao = %s, projeto_id = %s 
                WHERE id = %s
                """, (titulo.strip(), descricao.strip(), projeto_id, id_item))
            conn.commit()
            st.cache_data.clear()
            return True, "Anexo de desenho/projeto alterado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao editar item: {e}"

def excluir_desenho_projeto(id_item):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pdf_path FROM biblioteca_projetos WHERE id = %s", (id_item,))
            res = cursor.fetchone()
            if res and res[0] and os.path.exists(res[0]):
                try:
                    os.remove(res[0])
                except Exception:
                    pass
            cursor.execute("DELETE FROM biblioteca_projetos WHERE id = %s", (id_item,))
            conn.commit()
            st.cache_data.clear()
            return True, "Item excluído da biblioteca com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao excluir item: {e}"

def dar_entrada_nota_fiscal(numero_nf, fornecedor, cnpj, nome_prod, qtd_mov, valor_unit, usuario_logado, categoria="Geral", localizacao="Almoxarifado Principal"):
    valor_total = qtd_mov * valor_unit
    conn = conectar()
    try:
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
            return True, "Entrada por NF registrada com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao dar entrada na NF: {e}"

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

@st.cache_data(ttl=15)
def buscar_notas_fiscais():
    conn = conectar()
    return pd.read_sql_query("SELECT id, numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, valor_unitario, valor_total, data_recebimento, usuario FROM notas_fiscais ORDER BY id DESC", conn)

@st.cache_data(ttl=5)
def buscar_usuarios():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT id, usuario, perfil, ultima_atividade, localizacao_sessao, acao_atual,
    CASE WHEN ultima_atividade >= NOW() - INTERVAL '2 minutes' THEN 'Online'
    ELSE 'Offline' END as status_online
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
        conn.rollback()
        return False, f"Erro ao cadastrar usuário: {e}"

def editar_usuario(user_id, novo_nome, novo_perfil, nova_senha=""):
    try:
        conn = conectar()
        with conn.cursor() as cursor:
            if nova_senha.strip():
                hash_s = gerar_hash_senha(nova_senha)
                cursor.execute("UPDATE usuarios SET usuario = %s, perfil = %s, senha = %s WHERE id = %s", (novo_nome, novo_perfil, hash_s, user_id))
            else:
                cursor.execute("UPDATE usuarios SET usuario = %s, perfil = %s WHERE id = %s", (novo_nome, novo_perfil, user_id))
            conn.commit()
            st.cache_data.clear()
            return True, "Usuário atualizado com sucesso!"
    except Exception as e:
        conn.rollback()
        return False, f"Erro ao atualizar usuário: {e}"

def salvar_registro_checklist(usuario, pdf_path, observacao=""):
    conn = conectar()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO checklist_ferramentas (usuario, pdf_path, observacao) VALUES (%s, %s, %s)", (usuario, pdf_path, observacao))
            conn.commit()
            return True
    except Exception:
        conn.rollback()
        return False

@st.cache_data(ttl=15)
def buscar_historico():
    conn = conectar()
    return pd.read_sql_query("""
    SELECT h.id, p.nome as produto, p.categoria, h.tipo, h.quantidade, h.usuario, h.nome_retirou, h.projeto_nome, h.responsavel_epi, h.data_hora
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
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Gestão de Projetos e Equipe",
        "📁 Biblioteca de Desenhos / Projetos",
        "🔄 Retirada / Devolução de Materiais",
        "➕ Cadastrar Produto",
        "🦊 Fox Assistente"
    ]
else:
    opcoes_menu = [
        "📦 Consulta de Estoque",
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Gestão de Projetos e Equipe",
        "📁 Biblioteca de Desenhos / Projetos",
        "📋 Checklist de Ferramentas",
        "🔄 Retirada / Devolução de Materiais",
        "➕ Cadastrar Produto",
        "📑 Entrada de NF (XML Auto)",
        "🧾 Consultar NFs Subidas",
        "📥 Importar Dados (Excel / Sheets)",
        "📊 Dashboard Analytics (BI)",
        "📜 Histórico / Auditoria",
        "👥 Gerenciar Usuários",
        "🎨 Personalizar Empresa",
        "🦊 Fox Assistente"
    ]

st.sidebar.title(f"🏢 {config['nome_empresa']}")
st.sidebar.write(f"👤 **{st.session_state.usuario}** ({st.session_state.perfil})")
if config['logo_path'] and os.path.exists(config['logo_path']):
    st.sidebar.image(config['logo_path'], use_container_width=True)

if config['mapa_path'] and os.path.exists(config['mapa_path']):
    st.sidebar.write("---")
    st.sidebar.subheader("🗺️ Layout/Mapa")
    ext = os.path.splitext(config['mapa_path'])[1].lower()
    with open(config['mapa_path'], "rb") as f:
        bytes_file = f.read()
    st.sidebar.download_button(
        label="⬇️ Baixar Mapa do Almoxarifado",
        data=bytes_file,
        file_name=f"mapa_almoxarifado{ext}",
        mime="application/pdf" if ext == ".pdf" else "application/octet-stream"
    )

opcao = st.sidebar.radio("📍 Navegação", opcoes_menu)
atualizar_presenca_usuario(st.session_state.usuario, acao=f"Navegando em: {opcao}")

if st.sidebar.button("🚪 Sair / Logout"):
    atualizar_presenca_usuario(st.session_state.usuario, acao="Desconectado")
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""
    st.rerun()

# --- ABA 1: CONSULTA DE ESTOQUE ---
if "Consulta de Estoque" in opcao:
    st.title(f"📦 Controle de Estoque - {config['nome_empresa']}")
    df_prod = buscar_produtos()
    
    if not df_prod.empty:
        df_zerados = df_prod[df_prod['quantidade'] <= 0]
        df_alertas = df_prod[(df_prod['quantidade'] > 0) & (df_prod['quantidade'] <= df_prod['qtd_minima'])]
        
        if not df_zerados.empty:
            st.error(f"⚠️ **ATENÇÃO:** Existe(m) {len(df_zerados)} produto(s) com **ESTOQUE ZERADO**!")
        if not df_alertas.empty:
            st.warning(f"🔔 **ALERTA:** Existe(m) {len(df_alertas)} produto(s) atingindo a **QUANTIDADE MÍNIMA**!")
            
    bar_search = st.text_input("🔍 Bipar Código de Barras (Leitor USB/Bluetooth):", key="bar_search")
    
    if not df_prod.empty:
        df_prod['Status'] = df_prod.apply(lambda x: "ZERADO" if x['quantidade'] <= 0 else ("REPOR" if x['quantidade'] <= x['qtd_minima'] else "OK"), axis=1)
        
        # --- LÓGICA ATUALIZADA DE DIFERENCIAÇÃO DE UNIDADES/EMBALAGENS ---
        def formatar_saldo(row):
            medida = row['unidade_medida']
            fator = row['qtd_por_caixa'] if row['qtd_por_caixa'] > 0 else 1
            qtd = row['quantidade']
            
            if medida == 'Caixa':
                qtd_cx = qtd / fator
                return f"{qtd:.0f} un ({qtd_cx:.2f} CX)"
            elif medida == 'Pacote':
                qtd_pacote = qtd / fator
                return f"{qtd:.0f} un ({qtd_pacote:.2f} Pacotes)"
            elif medida == 'Metro':
                if fator > 1:
                    qtd_rolos = qtd / fator
                    return f"{qtd:.2f} Mts (~{qtd_rolos:.2f} Rolos de {fator}m)"
                return f"{qtd:.2f} Mts"
            elif medida == 'Rolo':
                total_mts = qtd * fator if fator > 1 else qtd
                return f"{qtd:.0f} Rolos ({total_mts:.2f} Mts)"
            else: # Unidade
                return f"{qtd:.0f} Unidades"
                
        df_prod['Saldo Formatado'] = df_prod.apply(formatar_saldo, axis=1)
    else:
        df_prod['Status'] = pd.Series(dtype='str')
        df_prod['Saldo Formatado'] = pd.Series(dtype='str')
        
    c_busca1, c_busca2 = st.columns([2, 1])
    with c_busca1:
        busca = st.text_input("🔎 Buscar por nome ou categoria:")
    with c_busca2:
        setores_cadastrados = sorted(df_prod['localizacao'].dropna().unique().tolist()) if not df_prod.empty else []
        setor_selecionado = st.selectbox("📍 Filtrar por Setor Cadastrado:", ["Todos"] + setores_cadastrados)
        
    if not df_prod.empty:
        if bar_search.strip():
            df_prod = df_prod[df_prod['codigo_barras'].astype(str) == bar_search.strip()]
        else:
            if busca:
                df_prod = df_prod[df_prod['nome'].str.contains(busca, case=False, na=False) | df_prod['categoria'].str.contains(busca, case=False, na=False)]
            if setor_selecionado != "Todos":
                df_prod = df_prod[df_prod['localizacao'] == setor_selecionado]
                
    col_tbl, col_exp = st.columns([4, 1])
    with col_tbl:
        cols_para_exibir = ['id', 'codigo_barras', 'nome', 'ca', 'categoria', 'localizacao', 'unidade_medida', 'qtd_minima', 'Saldo Formatado', 'Status']
        cols_existentes = [c for c in cols_para_exibir if c in df_prod.columns]
        st.dataframe(df_prod[cols_existentes], use_container_width=True)
    with col_exp:
        if not df_prod.empty:
            st.download_button(
                label="📊 Exportar Excel",
                data=gerar_excel_download(df_prod),
                file_name="estoque_atual.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
    if st.session_state.perfil == "Admin" and not df_prod.empty:
        st.write("---")
        st.subheader("🛠️ Ferramentas Avançadas de Admin (Edição / Mesclagem)")
        tab_editar, tab_mesclar = st.tabs(["✏️ Editar Item do Estoque", "🔀 Mesclar Itens Cadastrados"])
        
        with tab_editar:
            item_edit_id = st.selectbox("Selecione o Item para Editar:", df_prod['id'].tolist(), format_func=lambda x: f"ID #{x} - {df_prod[df_prod['id']==x]['nome'].values[0]}")
            if item_edit_id:
                prod_row = df_prod[df_prod['id'] == item_edit_id].iloc[0]
                with st.form("form_edit_admin", clear_on_submit=True):
                    ed_c1, ed_c2 = st.columns(2)
                    with ed_c1:
                        ed_nome = st.text_input("Nome do Produto", value=prod_row['nome'])
                        ed_categoria = st.text_input("Categoria", value=prod_row['categoria'])
                        ed_localizacao = st.text_input("Localização / Setor", value=prod_row['localizacao'])
                        ed_cod_barras = st.text_input("Código de Barras", value=str(prod_row['codigo_barras']))
                        ed_qtd_minima = st.number_input("Quantidade Mínima (Aviso de Compras)", min_value=0.0, value=float(prod_row.get('qtd_minima', 5.0)), step=1.0)
                    with ed_c2:
                        ed_unidade = st.selectbox("Unidade de Medida", ["Unidade", "Caixa", "Pacote", "Metro", "Rolo"], index=["Unidade", "Caixa", "Pacote", "Metro", "Rolo"].index(prod_row['unidade_medida']) if prod_row['unidade_medida'] in ["Unidade", "Caixa", "Pacote", "Metro", "Rolo"] else 0)
                        ed_qtd_cx = st.number_input("Qtd de Itens por Embalagem / Tamanho do Rolo (m)", min_value=1, value=int(prod_row['qtd_por_caixa']))
                        ed_quantidade = st.number_input("Quantidade em Estoque", min_value=0.0, value=float(prod_row['quantidade']), step=1.0)
                        ed_ca = st.text_input("C.A (Se aplicável)", value=str(prod_row['ca']))
                    
                    if st.form_submit_button("💾 Salvar Alterações"):
                        ok, msg = editar_produto(item_edit_id, ed_nome, ed_categoria, ed_localizacao, ed_quantidade, ed_unidade, ed_qtd_cx, prod_row['foto_path'], ed_cod_barras, ed_ca, ed_qtd_minima)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                            
        with tab_mesclar:
            st.caption("Junte o estoque e o histórico de dois ou mais registros idênticos em um único cadastro.")
            prod_destino_id = st.selectbox("Selecione o Produto DESTINO (Que será MANTIDO):", df_prod['id'].tolist(), format_func=lambda x: f"ID #{x} - {df_prod[df_prod['id']==x]['nome'].values[0]}")
            outros_prods = df_prod[df_prod['id'] != prod_destino_id]
            prods_origem_ids = st.multiselect("Selecione o(s) Produto(s) ORIGEM (Serão SOMADOS e EXCLUÍDOS):", outros_prods['id'].tolist(), format_func=lambda x: f"ID #{x} - {outros_prods[outros_prods['id']==x]['nome'].values[0]}")
            
            if st.button("🔀 Confirmar Mesclagem de Itens", type="primary"):
                if prods_origem_ids:
                    ok, msg = mesclar_produtos(prod_destino_id, prods_origem_ids)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Selecione ao menos um produto de origem para mesclar.")

# --- ABA DE PEDIDOS DE COMPRAS ---
elif "Pedidos de Compras" in opcao:
    st.title("🛒 Gerador de Pedidos de Compras & Itens Faltantes")
    st.caption("Esta aba monitora automaticamente os produtos com estoque zerado ou abaixo do limite mínimo cadastrado.")
    
    df_prod = buscar_produtos()
    if df_prod.empty:
        st.info("Nenum produto cadastrado.")
    else:
        df_faltantes = df_prod[df_prod['quantidade'] <= df_prod['qtd_minima']].copy()
        if df_faltantes.empty:
            st.success("✅ Todos os produtos estão com níveis normais de estoque!")
        else:
            df_faltantes['Status'] = df_faltantes['quantidade'].apply(lambda x: "ZERADO" if x <= 0 else "REPOR")
            df_faltantes['Qtd a Comprar (Sugestão)'] = df_faltantes.apply(lambda x: max(0.0, float(x['qtd_minima']) - float(x['quantidade'])), axis=1)
            
            c_kpi1, c_kpi2, c_kpi3 = st.columns(3)
            with c_kpi1:
                st.metric("Total de Itens p/ Reposição", len(df_faltantes))
            with c_kpi2:
                st.metric("Itens Totalmente Zerados", len(df_faltantes[df_faltantes['quantidade'] <= 0]))
            with c_kpi3:
                st.metric("Itens em Nível Crítico", len(df_faltantes[df_faltantes['quantidade'] > 0]))
                
            st.write("---")
            st.subheader("📋 Lista para Solicitação de Compras")
            cols_pedidos = ['id', 'nome', 'categoria', 'localizacao', 'quantidade', 'qtd_minima', 'Qtd a Comprar (Sugestão)', 'unidade_medida', 'Status']
            st.dataframe(df_faltantes[cols_pedidos], use_container_width=True)
            
            st.write("---")
            st.subheader("📥 Gerar Pedido / Exportar para o Setor de Compras")
            items_selecionados = st.multiselect(
                "Selecione os itens que deseja incluir neste pedido de compras:",
                options=df_faltantes['id'].tolist(),
                default=df_faltantes['id'].tolist(),
                format_func=lambda x: f"{df_faltantes[df_faltantes['id']==x]['nome'].values[0]} (Qtd Atual: {df_faltantes[df_faltantes['id']==x]['quantidade'].values[0]} | Comprar: {df_faltantes[df_faltantes['id']==x]['Qtd a Comprar (Sugestão)'].values[0]})"
            )
            
            if items_selecionados:
                df_export_pedidos = df_faltantes[df_faltantes['id'].isin(items_selecionados)][cols_pedidos]
                st.download_button(
                    label="⬇️ Baixar Pedido de Compras (.XLSX)",
                    data=gerar_excel_download(df_export_pedidos, nome_arquivo="pedido_de_compras.xlsx"),
                    file_name=f"pedido_compras_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary"
                )

# --- ABA DE GESTÃO DE PROJETOS E EQUIPE ---
elif "Gestão de Projetos" in opcao:
    st.title("🏗️ Cadastramento e Gestão de Projetos e Equipe")
    st.caption("Cadastre projetos ativos e defina a equipe de colaboradores associada a cada obra/serviço.")
    
    df_proj = buscar_projetos()
    df_eq = buscar_equipe_projeto()
    
    tab_proj, tab_eq = st.tabs(["📌 Projetos Cadastrados", "👥 Equipe por Projeto"])
    
    with tab_proj:
        st.subheader("➕ Cadastrar Novo Projeto")
        with st.form("form_novo_projeto", clear_on_submit=True):
            p_nome = st.text_input("Nome / Código do Projeto *", placeholder="Ex: Reforma Galpão B / Obra 104")
            p_desc = st.text_area("Descrição / Detalhes", placeholder="Ex: Instalações elétricas e manutenção hidráulica")
            if st.form_submit_button("💾 Salvar Projeto", type="primary"):
                if p_nome.strip():
                    ok, msg = cadastrar_projeto(p_nome, p_desc)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("O nome do projeto é obrigatório.")
                    
        st.write("---")
        st.subheader("📋 Lista de Projetos Registrados")
        st.dataframe(df_proj, use_container_width=True)
    with tab_eq:
        st.subheader("👤 Adicionar Colaborador ao Projeto")
        if df_proj.empty:
            st.info("Cadastre pelo menos um projeto antes de vincular a equipe.")
        else:
            with st.form("form_nova_equipe", clear_on_submit=True):
                p_select_id = st.selectbox("Selecione o Projeto:", df_proj['id'].tolist(), format_func=lambda x: df_proj[df_proj['id']==x]['nome_projeto'].values[0])
                m_nome = st.text_input("Nome Completo do Colaborador *")
                m_funcao = st.text_input("Função / Cargo", value="Operador", placeholder="Ex: Eletricista, Encanador, Supervisor")
                if st.form_submit_button("➕ Vincular Colaborador ao Projeto"):
                    if m_nome.strip():
                        ok, msg = adicionar_membro_equipe(p_select_id, m_nome, m_funcao)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.warning("O nome do colaborador é obrigatório.")
                        
            st.write("---")
            st.subheader("👥 Quadro de Colaboradores por Projeto")
            st.dataframe(df_eq, use_container_width=True)

# --- ABA: BIBLIOTECA DE DESENHOS / PROJETOS ---
elif "Biblioteca de Desenhos" in opcao:
    st.title("📁 Biblioteca de Desenhos e Projetos (PDF)")
    st.caption("Anexe e gerencie plantas, desenhos técnicos e documentos em PDF associados aos projetos.")
    df_bib = buscar_biblioteca_projetos()
    df_proj = buscar_projetos()
    tab_consultar, tab_anexar = st.tabs(["🔍 Consultar / Gerenciar Desenhos", "➕ Anexar Novo Desenho / Projeto (PDF)"])
    with tab_consultar:
        if df_bib.empty:
            st.info("Nenhum desenho ou projeto em PDF foi anexado até o momento.")
        else:
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                filtro_busca_pdf = st.text_input("🔎 Buscar por Título ou Descrição:")
            with col_f2:
                lista_proj_filtro = ["Todos os Projetos"] + (df_proj['nome_projeto'].tolist() if not df_proj.empty else [])
                filtro_proj_pdf = st.selectbox("📌 Filtrar por Projeto:", lista_proj_filtro)
            df_exibir_pdf = df_bib.copy()
            if filtro_busca_pdf.strip():
                df_exibir_pdf = df_exibir_pdf[
                    df_exibir_pdf['titulo'].str.contains(filtro_busca_pdf, case=False, na=False) |
                    df_exibir_pdf['descricao'].str.contains(filtro_busca_pdf, case=False, na=False)
                ]
            if filtro_proj_pdf != "Todos os Projetos":
                df_exibir_pdf = df_exibir_pdf[df_exibir_pdf['nome_projeto'] == filtro_proj_pdf]
            st.write("---")
            st.subheader(f"📑 Desenhos Encontrados ({len(df_exibir_pdf)})")
            for _, row_pdf in df_exibir_pdf.iterrows():
                with st.expander(f"📄 #{row_pdf['id']} - {row_pdf['titulo']} ({row_pdf['nome_projeto'] or 'Sem Projeto'})"):
                    c_det1, c_det2 = st.columns([3, 1])
                    with c_det1:
                        st.write(f"**Descrição:** {row_pdf['descricao'] or 'Sem descrição'}")
                        st.write(f"**Arquivo Original:** `{row_pdf['nome_arquivo_original']}`")
                        st.write(f"**Data de Upload:** {row_pdf['data_upload']} | **Enviado por:** {row_pdf['usuario']}")
                        if os.path.exists(row_pdf['pdf_path']):
                            with open(row_pdf['pdf_path'], "rb") as pdf_f:
                                bytes_pdf = pdf_f.read()
                            st.download_button(
                                label="⬇️ Baixar PDF",
                                data=bytes_pdf,
                                file_name=row_pdf['nome_arquivo_original'] or "projeto.pdf",
                                mime="application/pdf",
                                key=f"dl_pdf_{row_pdf['id']}"
                            )
                        else:
                            st.error("⚠️ O arquivo físico em PDF não foi encontrado no servidor.")
                    with c_det2:
                        st.markdown("**Ações do Item:**")
                        if st.button("✏️ Alterar", key=f"btn_alt_{row_pdf['id']}"):
                            st.session_state[f"edit_mode_{row_pdf['id']}"] = True
                        if st.button("🗑️ Excluir", key=f"btn_exc_{row_pdf['id']}", type="secondary"):
                            st.session_state[f"del_mode_{row_pdf['id']}"] = True
                    
                    if st.session_state.get(f"edit_mode_{row_pdf['id']}", False):
                        st.warning(f"✏️ Editando Item #{row_pdf['id']}")
                        with st.form(key=f"form_edit_pdf_{row_pdf['id']}"):
                            edit_tit = st.text_input("Título", value=row_pdf['titulo'])
                            edit_desc = st.text_area("Descrição", value=row_pdf['descricao'])
                            
                            p_opts = [None] + (df_proj['id'].tolist() if not df_proj.empty else [])
                            idx_p = p_opts.index(row_pdf['projeto_id']) if row_pdf['projeto_id'] in p_opts else 0
                            edit_proj_id = st.selectbox(
                                "Projeto Associado", 
                                p_opts, 
                                index=idx_p,
                                format_func=lambda x: "Sem Projeto Viculado" if x is None else df_proj[df_proj['id']==x]['nome_projeto'].values[0]
                            )
                            
                            novo_pdf_up = st.file_uploader("Substituir Arquivo PDF (Opcional)", type=["pdf"], key=f"up_edit_{row_pdf['id']}")
                            
                            col_f_btn1, col_f_btn2 = st.columns(2)
                            with col_f_btn1:
                                sub_edit = st.form_submit_button("💾 Salvar Alterações", type="primary")
                            with col_f_btn2:
                                sub_canc = st.form_submit_button("❌ Cancelar")
                            if sub_edit:
                                n_path, n_orig = None, None
                                if novo_pdf_up is not None:
                                    n_path = salvar_arquivo_seguro(novo_pdf_up, pasta_destino="biblioteca_pdf", tipo="pdf")
                                    n_orig = novo_pdf_up.name
                                ok, msg = editar_desenho_projeto(row_pdf['id'], edit_tit, edit_desc, edit_proj_id, n_path, n_orig)
                                if ok:
                                    st.success(msg)
                                    st.session_state[f"edit_mode_{row_pdf['id']}"] = False
                                    st.rerun()
                                else:
                                    st.error(msg)
                            elif sub_canc:
                                st.session_state[f"edit_mode_{row_pdf['id']}"] = False
                                st.rerun()
                    if st.session_state.get(f"del_mode_{row_pdf['id']}", False):
                        st.error(f"⚠️ Tem certeza que deseja excluir o anexo '{row_pdf['titulo']}'?")
                        c_del_sim, c_del_nao = st.columns(2)
                        with c_del_sim:
                            if st.button("🔴 Sim, Confirmar Exclusão", key=f"conf_del_sim_{row_pdf['id']}"):
                                ok, msg = excluir_desenho_projeto(row_pdf['id'])
                                if ok:
                                    st.success(msg)
                                    st.session_state[f"del_mode_{row_pdf['id']}"] = False
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with c_del_nao:
                            if st.button("🟢 Cancelar", key=f"conf_del_nao_{row_pdf['id']}"):
                                st.session_state[f"del_mode_{row_pdf['id']}"] = False
                                st.rerun()
    with tab_anexar:
        st.subheader("➕ Anexar Novo Arquivo PDF à Biblioteca")
        with st.form("form_novo_pdf_bib", clear_on_submit=True):
            f_titulo = st.text_input("Título do Desenho / Projeto *", placeholder="Ex: Planta Elétrica Galpão 02 - Rev 03")
            f_desc = st.text_area("Descrição / Notas Técnicas", placeholder="Ex: Desenho aprovado com especificações de calhas e condutores")
            
            p_opts_cad = [None] + (df_proj['id'].tolist() if not df_proj.empty else [])
            f_proj_id = st.selectbox(
                "Vincular a um Projeto (Opcional):", 
                p_opts_cad, 
                format_func=lambda x: "Sem Projeto Vinculado" if x is None else df_proj[df_proj['id']==x]['nome_projeto'].values[0]
            )
            
            f_file = st.file_uploader("Selecione o arquivo PDF *", type=["pdf"])
            
            if st.form_submit_button("📤 Salvar e Anexar na Biblioteca", type="primary"):
                if not f_titulo.strip():
                    st.warning("⚠️ O título do desenho/projeto é obrigatório.")
                elif f_file is None:
                    st.warning("⚠️ Por favor, selecione um arquivo em formato PDF.")
                else:
                    try:
                        caminho_salvo = salvar_arquivo_seguro(f_file, pasta_destino="biblioteca_pdf", tipo="pdf")
                        ok, msg = cadastrar_desenho_projeto(
                            f_titulo,
                            f_desc,
                            f_proj_id,
                            f_file.name,
                            caminho_salvo,
                            st.session_state.usuario
                        )
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    except Exception as e:
                        st.error(f"Erro ao processar o arquivo: {e}")

# --- ABA 2: CHECKLIST DE FERRAMENTAS ---
elif "Checklist de Ferramentas" in opcao:
    st.title("📋 Checklist de Ferramentas e Equipamentos")
    tab_chk, tab_cad_ferr = st.tabs(["📋 Realizar Checklist", "🛠️ Cadastro / Gestão de Ferramentas"])
    
    with tab_chk:
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
            st.subheader("✅ Lista de Verificação")
            with st.form("form_checklist", clear_on_submit=True):
                itens_checklist = []
                for idx, row in df_ferramentas.iterrows():
                    st.markdown(f"🛠️ **Item:** '{row['nome']}' | Local: *{row['localizacao']}*")
                    c1, c2 = st.columns([2, 3])
                    with c1:
                        status = st.selectbox(f"Status - {row['nome']}", ["OK/Operacional", "Defeituoso", "Em Manutenção", "Ausente"], key=f"status_{row['id']}")
                    with c2:
                        obs_item = st.text_input(f"Observações - {row['nome']}", key=f"obs_{row['id']}", placeholder="Detalhes de danos/avarias...")
                    itens_checklist.append({"ferramenta": row['nome'], "status": status, "obs": obs_item})
                
                st.write("---")
                obs_gerais = st.text_area("✍️ Observações Gerais da Inspeção:", placeholder="Informe detalhes do estado do carrinho, maletas, etc.")
                btn_gerar_chk = st.form_submit_button("📄 Finalizar Checklist e Gerar PDF", type="primary")
                
                if btn_gerar_chk:
                    try:
                        path_pdf = gerar_pdf_checklist("Checklist Diário", st.session_state.usuario, itens_checklist, obs_gerais)
                        salvar_registro_checklist(st.session_state.usuario, path_pdf, obs_gerais)
                        st.success("✅ Checklist concluído com sucesso! Relatório PDF gerado.")
                        with open(path_pdf, "rb") as f:
                            st.download_button(
                                label="⬇️ Baixar Relatório PDF Gerado",
                                data=f.read(),
                                file_name=os.path.basename(path_pdf),
                                mime="application/pdf"
                            )
                    except Exception as e:
                        st.error(f"Erro ao gerar relatório: {e}")

    with tab_cad_ferr:
        st.subheader("🛠️ Cadastrar Nova Ferramenta")
        with st.form("form_cad_ferramenta", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            with fc1:
                f_local = st.text_input("📍 Local", value="Almoxarifado")
                f_codigo = st.text_input("🔑 Código *", placeholder="Ex: FER-001")
            with fc2:
                f_nome = st.text_input("🛠️ Ferramenta *", placeholder="Ex: Furadeira de Impacto")
                f_marca = st.text_input("🏷️ Marca", placeholder="Ex: Bosch")
            
            if st.form_submit_button("💾 Salvar Ferramenta", type="primary"):
                if f_codigo.strip() and f_nome.strip():
                    ok, msg = cadastrar_ferramenta(f_local, f_codigo, f_nome, f_marca)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("⚠️ Código e Nome da Ferramenta são obrigatórios!")
        
        st.write("---")
        st.subheader("📋 Ferramentas Cadastradas e Status de Empréstimo")
        df_f = buscar_ferramentas()
        if not df_f.empty:
            st.dataframe(df_f[['id', 'local', 'codigo', 'ferramenta', 'marca', 'funcionario', 'ultimo_funcionario', 'data_ultimo_emprestimo']], use_container_width=True)
        else:
            st.info("Nenhuma ferramenta cadastrada no controle individual.")

# --- ABA 3: RETIRADA E DEVOLUÇÃO DE MATERIAIS ---
elif "Retirada / Devolução" in opcao:
    st.title("🔄 Retirada / Devolução de Materiais e Ferramentas")
    df_prod = buscar_produtos()
    df_proj = buscar_projetos()
    df_eq = buscar_equipe_projeto()
    df_f = buscar_ferramentas()

    tab_mat, tab_ferr_retirada = st.tabs(["📦 Materiais e Insumos", "🔧 Retirada / Devolução de Ferramentas"])

    with tab_mat:
        if df_prod.empty:
            st.info("Nenhum produto cadastrado.")
        else:
            st.subheader("1. Tipo de Operação")
            tipo_operacao = st.radio("Selecione a Ação:", ["📤 Retirada (Saída)", "📥 Devolução (Entrada/Reinserção)"], horizontal=True)
            tipo_mov_banco = "SAÍDA" if "Retirada" in tipo_operacao else "DEVOLUÇÃO"
            
            st.write("---")
            st.subheader("2. Selecionar Produto")
            cod_bipado = st.text_input("🔍 Bipar Código de Barras para Selecionar Rápidamente:", key="retirada_cod_bipado")
            
            prod_selecionado = None
            if cod_bipado.strip():
                match = df_prod[df_prod['codigo_barras'].astype(str) == cod_bipado.strip()]
                if not match.empty:
                    prod_selecionado = match.iloc[0]['nome']
                    st.success(f"Item Encontrado: **{prod_selecionado}**")
                else:
                    st.warning("Código de barras não encontrado no cadastro.")
                    
            if not prod_selecionado:
                prod_selecionado = st.selectbox("Ou Escolha o Item na Lista:", df_prod['nome'].tolist(), key="retirada_select_item")
                
            row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
            
            st.write("---")
            st.subheader(f"3. Dados para {tipo_operacao}")
            col1, col2 = st.columns(2)
            
            with col1:
                st.info(f"📦 **Item:** {row['nome']} \n\n🏷️ **Categoria:** {row['categoria']} \n\n📊 **Estoque Atual:** {row['quantidade']} ({row['unidade_medida']})")
                
                # --- AJUSTE NA RETIRADA DE ACORDO COM A UNIDADE ---
                medida_item = row['unidade_medida']
                fator_emb = row['qtd_por_caixa'] if row['qtd_por_caixa'] > 0 else 1
                
                if medida_item == "Caixa":
                    modo_medida = st.radio("Modo da Operação:", ["Por Unidade", "Por Caixa"], horizontal=True, key="retirada_modo_medida")
                    if modo_medida == "Por Caixa":
                        qtd_input = st.number_input(f"Qtd em Caixas (Cada caixa contém {fator_emb} un):", min_value=0.1, step=1.0, value=1.0)
                        qtd_mov_final = qtd_input * fator_emb
                        st.caption(f"Total a movimentar no estoque: **{qtd_mov_final:.2f} Unidades**")
                    else:
                        qtd_mov_final = st.number_input("Qtd em Unidades:", min_value=0.1, step=1.0, value=1.0)
                elif medida_item == "Pacote":
                    modo_medida = st.radio("Modo da Operação:", ["Por Unidade Avulsa", "Por Pacote Fechado"], horizontal=True, key="retirada_modo_medida")
                    if modo_medida == "Por Pacote Fechado":
                        qtd_input = st.number_input(f"Qtd em Pacotes (Cada pacote contém {fator_emb} un):", min_value=0.1, step=1.0, value=1.0)
                        qtd_mov_final = qtd_input * fator_emb
                        st.caption(f"Total a movimentar no estoque: **{qtd_mov_final:.2f} Unidades**")
                    else:
                        qtd_mov_final = st.number_input("Qtd em Unidades:", min_value=0.1, step=1.0, value=1.0)
                elif medida_item in ["Metro", "Rolo"]:
                    modo_medida = st.radio("Modo da Operação:", ["Por Metros", "Por Rolos Fechados"], horizontal=True, key="retirada_modo_medida")
                    if modo_medida == "Por Rolos Fechados":
                        qtd_input = st.number_input(f"Qtd de Rolos (Cada rolo tem {fator_emb}m):", min_value=0.1, step=1.0, value=1.0)
                        qtd_mov_final = qtd_input * fator_emb
                        st.caption(f"Total a movimentar no estoque: **{qtd_mov_final:.2f} Metros**")
                    else:
                        qtd_mov_final = st.number_input("Qtd em Metros:", min_value=0.1, step=0.5, value=1.0)
                else: # Unidade
                    qtd_mov_final = st.number_input("Qtd em Unidades:", min_value=0.1, step=1.0, value=1.0, key="retirada_qtd_un")
                
                eh_epi = "EPI" in str(row['categoria']).upper() or "EPI" in str(row['nome']).upper()
                responsavel_epi = ""
                
            with col2:
                st.write("📋 **Destinação e Responsável:**")
                
                lista_projetos = ["Geral / Sem Projeto Específico"] + (df_proj['nome_projeto'].tolist() if not df_proj.empty else [])
                projeto_selecionado = st.selectbox("🏗️ Projeto Destino:", lista_projetos)
                
                colaboradores_sugeridos = []
                if projeto_selecionado != "Geral / Sem Projeto Específico" and not df_eq.empty:
                    colaboradores_sugeridos = df_eq[df_eq['nome_projeto'] == projeto_selecionado]['nome_colaborador'].tolist()
                
                nome_retirou = st.text_input("👤 Nome de Quem Retirou *:", placeholder="Informe o nome da pessoa que está pegando o material")
                if colaboradores_sugeridos:
                    st.caption(f"💡 Sugestões da equipe do projeto: {', '.join(colaboradores_sugeridos)}")
                if eh_epi:
                    st.warning("⚠️ **ESTE ITEM É UM EPI!**")
                    if row.get('ca'):
                        st.info(f"🛡️ **Número do CA do EPI:** {row['ca']}")
                    responsavel_epi = st.text_input("👤 Matrícula/Nome p/ Registro de EPI:", value=nome_retirou, key="retirada_resp_epi")
            st.write("---")
            btn_label = "📤 Confirmar Retirada de Item" if tipo_mov_banco == "SAÍDA" else "📥 Confirmar Devolução e Recompor Estoque"
            
            if st.button(btn_label, type="primary"):
                if tipo_mov_banco == "SAÍDA" and not nome_retirou.strip():
                    st.error("❌ Por favor, informe o **Nome de quem retirou** o material!")
                elif eh_epi and not responsavel_epi.strip():
                    st.error("❌ Erro: Preencha a verificação de EPI!")
                else:
                    ok, msg = movimentar_produto(
                        int(row['id']),
                        tipo_mov_banco,
                        float(qtd_mov_final),
                        float(row['quantidade']),
                        st.session_state.usuario,
                        responsavel_epi,
                        nome_retirou,
                        projeto_selecionado
                    )
                    if ok:
                        st.success(f"✅ Operação realizada com sucesso! {msg}")
                        if "retirada_cod_bipado" in st.session_state:
                            del st.session_state["retirada_cod_bipado"]
                        if "retirada_resp_epi" in st.session_state:
                            del st.session_state["retirada_resp_epi"]
                        st.rerun()
                    else:
                        st.error(msg)

    with tab_ferr_retirada:
        st.subheader("🔧 Movimentação de Ferramentas")
        
        acao_ferr = st.radio("Selecione a Operação de Ferramentas:", ["📤 Retirada de Ferramenta", "📥 Devolução para Almoxarifado"], horizontal=True)
        
        if df_f.empty:
            st.info("Nenhuma ferramenta cadastrada. Acesse a aba 'Checklist de Ferramentas' -> 'Cadastro / Gestão de Ferramentas' para cadastrar.")
        else:
            with st.form("form_mov_ferramenta", clear_on_submit=True):
                codigo_ferr = st.selectbox(
                    "🔑 Selecione a Ferramenta pelo Código:",
                    options=df_f['codigo'].tolist(),
                    format_func=lambda x: f"{x} - {df_f[df_f['codigo']==x]['ferramenta'].values[0]} ({'Empréstimo: ' + df_f[df_f['codigo']==x]['funcionario'].values[0] if df_f[df_f['codigo']==x]['funcionario'].values[0] != 'Almoxarifado' else 'Disponível no Almoxarifado'})"
                )
                
                if "Retirada" in acao_ferr:
                    nome_func_novo = st.text_input("👤 Nome do Funcionário que está Retirando *:", placeholder="Informe o nome de quem ficará com a ferramenta")
                else:
                    st.info("ℹ️ A ferramenta selecionada retornará para o status **'Almoxarifado'** (não em empréstimo).")
                    nome_func_novo = "Almoxarifado"
                
                if st.form_submit_button("🚀 Confirmar Movimentação de Ferramenta", type="primary"):
                    if "Retirada" in acao_ferr and not nome_func_novo.strip():
                        st.warning("⚠️ Informe o nome do funcionário para retirar a ferramenta.")
                    else:
                        ok, msg = movimentar_ferramenta(codigo_ferr, nome_func_novo)
                        if ok:
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(msg)

# --- ABA 4: CADASTRO DE PRODUTO ---
elif "Cadastrar Produto" in opcao:
    st.title("➕ Cadastrar Novo Produto")
    with st.form("form_cad_prod", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            nome = st.text_input("📦 Nome do Produto *")
            codigo_barras = st.text_input("🔍 Código de Barras (Bipar ou Digitar)")
            categoria = st.text_input("🏷️ Categoria (Ex: Ferramenta, Equipamento, EPI, Elétrica)", value="Geral")
            ca = st.text_input("🛡️ Número do CA (Certificado de Aprovação - Apenas para EPIs)")
            localizacao = st.text_input("📍 Localização / Corredor/Prateleira", value="Almoxarifado Principal")
            qtd_minima = st.number_input("🔔 Quantidade Mínima (Aviso de Compras)", min_value=0.0, step=1.0, value=5.0)
        with c2:
            # --- OPÇÕES AMPLIADAS DE UNIDADES DE MEDIDA ---
            unidade_medida = st.selectbox(
                "📏 Unidade de Medida", 
                ["Unidade", "Caixa", "Pacote", "Metro", "Rolo"]
            )
            
            qtd_por_caixa = 1
            if unidade_medida == "Caixa":
                qtd_por_caixa = st.number_input("📦 Qtd de Itens (Volumes Únicos) dentro de 1 Caixa", min_value=1, value=1)
            elif unidade_medida == "Pacote":
                qtd_por_caixa = st.number_input("📦 Qtd de Itens (Volumes Únicos) dentro de 1 Pacote", min_value=1, value=1)
            elif unidade_medida in ["Metro", "Rolo"]:
                qtd_por_caixa = st.number_input("📏 Metragem Padrão de cada Rolo (em Metros)", min_value=1, value=100)
            quantidade = st.number_input("📊 Quantidade Inicial em Estoque", min_value=0.0, step=1.0, value=0.0)
            foto = st.file_uploader("🖼️ Foto do Produto (Opcional)", type=["jpg", "png", "jpeg"])
            
        sub = st.form_submit_button("➕ Cadastrar Produto")
        if sub:
            if nome.strip():
                foto_path = ""
                if foto is not None:
                    foto_path = salvar_arquivo_seguro(foto, tipo="imagem")
                ok, msg = cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.warning("⚠️ O nome do produto é obrigatório!")

# --- ABA 5: ENTRADA POR NOTA FISCAL ---
elif "Entrada de NF" in opcao:
    st.title("📑 Recebimento e Entrada por Nota Fiscal")
    aba_xml, aba_manual = st.tabs(["📂 Importar Arquivo XML (Automático)", "✍️ Lançamento Manual"])
    
    with aba_xml:
        uploaded_xml = st.file_uploader("Arraste ou selecione o arquivo .xml da Nota Fiscal:", type=["xml"])
        if uploaded_xml is not None:
            sucesso, dados_nfe = processar_xml_nfe(uploaded_xml)
            if sucesso:
                st.success(f"✅ XML lido com sucesso! Nota Fiscal **{dados_nfe['numero_nf']}**")
                st.write(f"🏢 **Fornecedor:** {dados_nfe['fornecedor']} | **CNPJ:** {dados_nfe['cnpj']}")
                
                st.subheader("Selecione os itens que deseja cadastrar/atualizar no estoque:")
                df_itens = pd.DataFrame(dados_nfe['itens'])
                df_itens.insert(0, "Selecionar", True)
                
                df_itens_editado = st.data_editor(
                    df_itens,
                    column_config={"Selecionar": st.column_config.CheckboxColumn("Cadastrar?", default=True)},
                    disabled=["produto", "quantidade", "valor_unitario", "valor_total"],
                    hide_index=True,
                    use_container_width=True
                )
                
                if st.button("🚀 Confirmar e Dar Entrada Automática nos Itens Selecionados", type="primary"):
                    itens_selecionados = df_itens_editado[df_itens_editado["Selecionar"] == True]
                    if itens_selecionados.empty:
                        st.warning("⚠️ Nenhum item foi selecionado para entrada!")
                    else:
                        count_sucesso = 0
                        erros = []
                        for idx_i, item in itens_selecionados.iterrows():
                            ok, msg = dar_entrada_nota_fiscal(
                                dados_nfe['numero_nf'],
                                dados_nfe['fornecedor'],
                                dados_nfe['cnpj'],
                                item['produto'],
                                float(item['quantidade']),
                                float(item['valor_unitario']),
                                st.session_state.usuario
                            )
                            if ok:
                                count_sucesso += 1
                            else:
                                erros.append(f"Erro no item {item['produto']}: {msg}")
                        
                        if count_sucesso > 0:
                            st.success(f"✅ Entrada concluída! {count_sucesso} itens selecionados foram atualizados/cadastrados.")
                        if erros:
                            for err in erros:
                                st.error(err)
                        st.rerun()
            else:
                st.error(dados_nfe)
    with aba_manual:
        with st.form("form_nf_manual", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                num_nf = st.text_input("📄 Número da Nota Fiscal")
                fornecedor = st.text_input("🏢 Fornecedor / Empresa")
                cnpj = st.text_input("🆔 CNPJ (Opcional)")
                nome_prod = st.text_input("📦 Nome do Produto")
            with c2:
                qtd_nf = st.number_input("📊 Quantidade Recebida", min_value=0.1, step=1.0)
                val_unit = st.number_input("💵 Valor Unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
                st.write(f"💰 **Valor Total Estimado:** R$ {qtd_nf * val_unit:.2f}")
                
            if st.form_submit_button("💾 Salvar e Dar Entrada"):
                if num_nf and fornecedor and nome_prod:
                    ok, msg = dar_entrada_nota_fiscal(num_nf, fornecedor, cnpj, nome_prod, float(qtd_nf), float(val_unit), st.session_state.usuario)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

# --- ABA 6: CONSULTAR NFS SUBIDAS ---
elif "Consultar NFs" in opcao:
    st.title("🧾 Relatório e Consulta de Notas Fiscais Lançadas")
    df_nf = buscar_notas_fiscais()
    if df_nf.empty:
        st.info("Nenhuma Nota Fiscal cadastrada até o momento.")
    else:
        st.subheader("🔍 Filtro de Buscas")
        c1, c2 = st.columns(2)
        with c1:
            busca_nf = st.text_input("Buscar por Número da NF ou Fornecedor:")
        with c2:
            busca_prod_nf = st.text_input("Buscar por Produto na NF:")
            
        if busca_nf:
            df_nf = df_nf[df_nf['numero_nf'].astype(str).str.contains(busca_nf, case=False, na=False) | df_nf['fornecedor'].str.contains(busca_nf, case=False, na=False)]
        if busca_prod_nf:
            df_nf = df_nf[df_nf['produto_nome'].str.contains(busca_prod_nf, case=False, na=False)]
            
        col_t, col_e = st.columns([4, 1])
        with col_t:
            st.dataframe(df_nf, use_container_width=True)
        with col_e:
            st.download_button(
                label="📊 Exportar NFs (Excel)",
                data=gerar_excel_download(df_nf, nome_arquivo="notas_fiscais.xlsx"),
                file_name="notas_fiscais.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# --- ABA 7: IMPORTAR DADOS ---
elif "Importar Dados" in opcao:
    st.title("📥 Importação em Lote de Produtos")
    tab_excel, tab_sheets = st.tabs(["📊 Importar de Planilha Excel (.xlsx)", "🌐 Importar de Google Sheets"])
    
    with tab_excel:
        file_excel = st.file_uploader("Selecione o arquivo .xlsx:", type=["xlsx", "xls"])
        if file_excel is not None:
            try:
                df_imp = pd.read_excel(file_excel)
                st.dataframe(df_imp, use_container_width=True)
                if st.button("🚀 Confirmar e Importar para o Banco", type="primary"):
                    qtd_importados = 0
                    erros = 0
                    for idx, row in df_imp.iterrows():
                        ok, _ = cadastrar_produto(
                            str(row['nome']),
                            str(row.get('categoria', 'Geral')),
                            str(row.get('localizacao', 'Não informada')),
                            float(row.get('quantidade', 0)),
                            str(row.get('unidade_medida', 'Unidade')),
                            int(row.get('qtd_por_caixa', 1)),
                            codigo_barras=str(row.get('codigo_barras', "")),
                            ca=str(row.get('ca', "")),
                            qtd_minima=float(row.get('qtd_minima', 5.0))
                        )
                        if ok:
                            qtd_importados += 1
                        else:
                            erros += 1
                    st.success(f"✅ {qtd_importados} produtos cadastrados com sucesso!")
                    if erros > 0:
                        st.warning(f"⚠️ {erros} itens não puderam ser importados.")
                    st.rerun()
            except Exception as e:
                st.error(f"Erro ao ler arquivo Excel: {e}")
    with tab_sheets:
        sheet_id = st.text_input("🆔 ID da Planilha do Google Sheets:")
        if sheet_id:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
            try:
                df_gsheets = pd.read_csv(url)
                st.dataframe(df_gsheets, use_container_width=True)
                if st.button("🚀 Importar Dados do Google Sheets", type="primary"):
                    qtd_importados = 0
                    for idx, row in df_gsheets.iterrows():
                        cadastrar_produto(
                            str(row['nome']),
                            str(row.get('categoria', 'Geral')),
                            str(row.get('localizacao', 'Não informada')),
                            float(row.get('quantidade', 0)),
                            str(row.get('unidade_medida', 'Unidade')),
                            int(row.get('qtd_por_caixa', 1)),
                            codigo_barras=str(row.get('codigo_barras', "")),
                            ca=str(row.get('ca', "")),
                            qtd_minima=float(row.get('qtd_minima', 5.0))
                        )
                        qtd_importados += 1
                    st.success(f"✅ {qtd_importados} produtos importados!")
                    st.rerun()
            except Exception as e:
                st.error(f"Erro ao acessar planilha: {e}")

# --- ABA 8: DASHBOARD BI ---
elif "Dashboard Analytics" in opcao:
    st.title("📊 Dashboard Analytics (BI) - Inteligência do Almoxarifado")
    df_prod = buscar_produtos()
    df_hist = buscar_historico()
    df_nf = buscar_notas_fiscais()
    df_proj = buscar_projetos()
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.metric("Total de Produtos", len(df_prod))
    with kpi2:
        itens_criticos = len(df_prod[df_prod['quantidade'] <= df_prod['qtd_minima']]) if not df_prod.empty else 0
        st.metric("Itens com Estoque Baixo", itens_criticos, delta_color="inverse")
    with kpi3:
        total_retiradas = len(df_hist[df_hist['tipo'] == 'SAÍDA']) if not df_hist.empty else 0
        st.metric("Total de Retiradas", total_retiradas)
    with kpi4:
        if not df_hist.empty:
            df_hist['data_hora'] = pd.to_datetime(df_hist['data_hora'])
            sete_dias_atras = datetime.now() - timedelta(days=7)
            df_7dias = df_hist[(df_hist['data_hora'] >= sete_dias_atras) & (df_hist['projeto_nome'] != '') & (df_hist['projeto_nome'] != 'Geral / Sem Projeto Específico')]
            projetos_7d = df_7dias['projeto_nome'].nunique()
        else:
            projetos_7d = 0
        st.metric("Projetos Ativos (Últimos 7 Dias)", projetos_7d)
    st.write("---")
    st.subheader("🏗️ Levantamento e Consumo por Projeto")
    if not df_hist.empty:
        df_saidas_proj = df_hist[(df_hist['tipo'] == 'SAÍDA') & (df_hist['projeto_nome'] != '')]
        if not df_saidas_proj.empty:
            lista_proj_bi = ["Todos os Projetos"] + sorted(df_saidas_proj['projeto_nome'].unique().tolist())
            proj_filtro = st.selectbox("Selecione um Projeto para Levantamento Detalhado:", lista_proj_bi)
            
            if proj_filtro != "Todos os Projetos":
                df_saidas_proj = df_saidas_proj[df_saidas_proj['projeto_nome'] == proj_filtro]
            
            st.write(f"📦 **Materiais Retirados para o Projeto:**")
            lev_proj = df_saidas_proj.groupby(['produto', 'nome_retirou'])['quantidade'].sum().reset_index()
            st.dataframe(lev_proj, use_container_width=True)
            st.bar_chart(df_saidas_proj.groupby('produto')['quantidade'].sum())
        else:
            st.info("Nenhuma saída vinculada a projetos no histórico.")
    else:
        st.info("Sem dados de histórico de retiradas.")
    st.write("---")
    st.subheader("💰 Análise Financeira de Gastos e Comparação por Períodos")
    if not df_nf.empty:
        df_nf['data_recebimento'] = pd.to_datetime(df_nf['data_recebimento'])
        tab_periodo, tab_comparativo = st.tabs(["📅 Gastos em Período Específico", "📈 Comparativo entre Períodos / 12 Meses"])
        
        with tab_periodo:
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                data_inicio = st.date_input("Data Inicial:", df_nf['data_recebimento'].min().date())
            with col_d2:
                data_fim = st.date_input("Data Final:", df_nf['data_recebimento'].max().date())
            
            mask = (df_nf['data_recebimento'].dt.date >= data_inicio) & (df_nf['data_recebimento'].dt.date <= data_fim)
            df_filtrado = df_nf[mask]
            valor_total_gastos = df_filtrado['valor_total'].sum()
            
            st.metric("Total Gasto no Período Selecionado", f"R$ {valor_total_gastos:,.2f}")
            st.dataframe(df_filtrado[['numero_nf', 'fornecedor', 'produto_nome', 'quantidade', 'valor_unitario', 'valor_total', 'data_recebimento']], use_container_width=True)
        with tab_comparativo:
            modo_comp = st.radio("Selecione o Tipo de Comparação:", ["Últimos 12 Meses", "Comparar Dois Períodos Customizados"], horizontal=True)
            if modo_comp == "Últimos 12 Meses":
                df_nf['Ano_Mês'] = df_nf['data_recebimento'].dt.to_period('M')
                gastos_mensais = df_nf.groupby('Ano_Mês')['valor_total'].sum().reset_index()
                gastos_mensais['Ano_Mês'] = gastos_mensais['Ano_Mês'].astype(str)
                gastos_mensais = gastos_mensais.tail(12)
                st.bar_chart(gastos_mensais.set_index('Ano_Mês'))
            else:
                st.write("**Período A:**")
                ca1, ca2 = st.columns(2)
                with ca1:
                    p1_ini = st.date_input("Início Período A", df_nf['data_recebimento'].min().date(), key="p1_ini")
                with ca2:
                    p1_fim = st.date_input("Fim Período A", df_nf['data_recebimento'].max().date(), key="p1_fim")
                
                st.write("**Período B:**")
                cb1, cb2 = st.columns(2)
                with cb1:
                    p2_ini = st.date_input("Início Período B", df_nf['data_recebimento'].min().date(), key="p2_ini")
                with cb2:
                    p2_fim = st.date_input("Fim Período B", df_nf['data_recebimento'].max().date(), key="p2_fim")
                    
                gasto_a = df_nf[(df_nf['data_recebimento'].dt.date >= p1_ini) & (df_nf['data_recebimento'].dt.date <= p1_fim)]['valor_total'].sum()
                gasto_b = df_nf[(df_nf['data_recebimento'].dt.date >= p2_ini) & (df_nf['data_recebimento'].dt.date <= p2_fim)]['valor_total'].sum()
                
                col_mc1, col_mc2 = st.columns(2)
                col_mc1.metric("Gasto Período A", f"R$ {gasto_a:,.2f}")
                col_mc2.metric("Gasto Período B", f"R$ {gasto_b:,.2f}", delta=f"R$ {gasto_b - gasto_a:,.2f}", delta_color="inverse")
    else:
        st.info("Nenhuma Nota Fiscal cadastrada para exibição de dados financeiros.")

# --- ABA 9: HISTÓRICO E ESTORNO ---
elif "Histórico / Auditoria" in opcao:
    st.title("📜 Histórico e Auditoria de Movimentações (Retenção 18 Meses)")
    df_hist = buscar_historico()
    col_h1, col_h2 = st.columns([4, 1])
    with col_h1:
        st.dataframe(df_hist, use_container_width=True)
    with col_h2:
        if not df_hist.empty:
            st.download_button(
                label="📊 Exportar Excel",
                data=gerar_excel_download(df_hist),
                file_name="historico_movimentacoes.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    st.write("---")
    st.subheader("↩️ Estorno / Correção dos Últimos Registros")
    if not df_hist.empty:
        ultimos_10 = df_hist.head(10)
        for idx, row in ultimos_10.iterrows():
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                st.write(f"🆔 **ID #{row['id']}** | Item: '{row['produto']}' | Retirou: `{row['nome_retirou']}` | Projeto: `{row['projeto_nome']}` | Qtd: `{row['quantidade']}` | Data: '{row['data_hora']}'")
            with col_btn:
                desabilitado = "ESTORNO" in str(row['tipo']).upper()
                if st.button(f"↩️ Estornar #{row['id']}", key=f"estorno_{row['id']}", disabled=desabilitado):
                    try:
                        ok, msg = estornar_movimentacao(int(row['id']), st.session_state.usuario)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    except Exception as err:
                        st.error(f"Erro inesperado ao realizar o estorno: {err}")

# --- ABA 10: GERENCIAR USUÁRIOS (ADMIN) ---
elif "Gerenciar Usuários" in opcao and st.session_state.perfil == "Admin":
    st.title("👥 Gerenciamento de Usuários e Monitoramento")
    df_usr = buscar_usuarios()
    st.subheader("🌐 Status dos Usuários Online e Atividades")
    st.dataframe(df_usr[['id', 'usuario', 'perfil', 'status_online', 'ultima_atividade', 'localizacao_sessao', 'acao_atual']], use_container_width=True)
    
    st.write("---")
    st.subheader("✏️ Editar Usuário / Redefinir Senha")
    user_edit_id = st.selectbox("Selecione o Usuário para Editar:", df_usr['id'].tolist(), format_func=lambda x: f"ID #{x} - {df_usr[df_usr['id']==x]['usuario'].values[0]}")
    if user_edit_id:
        usr_row = df_usr[df_usr['id'] == user_edit_id].iloc[0]
        with st.form("form_edit_usr", clear_on_submit=True):
            ed_u_nome = st.text_input("👤 Nome do Usuário", value=usr_row['usuario'])
            ed_u_perfil = st.selectbox("🛡️ Perfil de Acesso", ["Operador", "Admin"], index=["Operador", "Admin"].index(usr_row['perfil']))
            ed_u_senha = st.text_input("🔑 Nova Senha (Deixe em branco para manter a senha atual)", type="password")
            
            if st.form_submit_button("💾 Salvar Alterações no Usuário"):
                ok, msg = editar_usuario(user_edit_id, ed_u_nome, ed_u_perfil, ed_u_senha)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    st.write("---")
    st.subheader("➕ Cadastrar Novo Usuário")
    with st.form("form_cad_usr", clear_on_submit=True):
        u_nome = st.text_input("👤 Nome do Usuário")
        u_senha = st.text_input("🔑 Senha", type="password")
        u_perfil = st.selectbox("🛡️ Perfil de Acesso", ["Operador", "Admin"])
        
        if st.form_submit_button("➕ Cadastrar Usuário"):
            ok, msg = cadastrar_usuario(u_nome, u_senha, u_perfil)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

# --- ABA 11: PERSONALIZAR EMPRESA (ADMIN) ---
elif "Personalizar Empresa" in opcao and st.session_state.perfil == "Admin":
    st.title("🎨 Configurações da Empresa e Layout")
    with st.form("form_cfg", clear_on_submit=True):
        e_nome = st.text_input("🏢 Nome da Empresa", value=config['nome_empresa'])
        e_cor = st.color_picker("🎨 Cor do Tema", value=config['cor_tema'])
        e_logo = st.file_uploader("🖼️ Atualizar Logomarca (Imagem)", type=["png", "jpg", "jpeg"])
        e_mapa = st.file_uploader("🗺️ Atualizar Mapa do Almoxarifado (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])
        
        if st.form_submit_button("💾 Salvar Configurações"):
            logo_p = config['logo_path']
            mapa_p = config['mapa_path']
            if e_logo is not None:
                logo_p = salvar_arquivo_seguro(e_logo, tipo="imagem")
            if e_mapa is not None:
                mapa_p = salvar_arquivo_seguro(e_mapa, tipo="mapa")
                
            ok, msg = salvar_configuracoes(e_nome, logo_p, e_cor, mapa_p)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

# --- ABA 12: FOX ASSISTENTE (GEMINI AI) ---
elif "Fox Assistente" in opcao:
    st.title("🦊 Fox Assistente - Almoxarifado Inteligente")
    st.caption("Sua assistente integrada que lê o banco de dados, compara preços, analisa BI e tira dúvidas!")
    
    if not client_gemini:
        st.error("Chave de API do Gemini não configurada em .streamlit/secrets.toml (GEMINI_API_KEY).")
    else:
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []
            
        df_p_ctx = buscar_produtos()
        df_nf_ctx = buscar_notas_fiscais()
        df_hist_ctx = buscar_historico()
        
        resumo_produtos = df_p_ctx[['nome', 'quantidade', 'unidade_medida', 'localizacao']].to_string(index=False) if not df_p_ctx.empty else "Nenhum produto"
        resumo_precos = df_nf_ctx[['produto_nome', 'fornecedor', 'valor_unitario', 'valor_total', 'data_recebimento']].head(20).to_string(index=False) if not df_nf_ctx.empty else "Nenhuma nota fiscal"
        resumo_saidas = df_hist_ctx[['produto', 'quantidade', 'nome_retirou', 'projeto_nome', 'data_hora']].head(20).to_string(index=False) if not df_hist_ctx.empty else "Nenhum histórico"
        
        FOX_SYSTEM_INSTRUCTION_DINAMICO = f"""
Você é a Fox Assistente, a inteligência virtual simpática e especialista do Sistema de Almoxarifado! 🦊✨
### VISÃO GERAL DAS OPERAÇÕES DO SISTEMA:
1. **Consulta de Estoque:** Exibe produtos, quantidades, localizações e avisa quando itens estão com nível crítico ou zerados.
2. **Pedidos de Compras:** Gera automaticamente listas de reposição com sugestão de compra em Excel.
3. **Gestão de Projetos e Equipe:** Permite cadastrar obras/projetos e atribuir colaboradores para rastrear o uso dos materiais.
4. **Biblioteca de Desenhos / Projetos:** Anexa, consulta, altera e remove plantas e projetos em PDF.
5. **Checklist de Ferramentas:** Ferramenta diária para fiscalizar o estado operacional de ferramentas e gerar relatórios em PDF.
6. **Retirada / Devolução:** Registra saída/entrada de materiais exigindo obrigatoriamente o Nome de quem retirou, Projeto e responsável por EPI.
7. **Entrada de NF:** Importa arquivos XML de NFe ou faz lançamentos manuais com atualização instantânea de saldo e preço.
8. **Dashboard Analytics (BI):** Apresenta comparativos de gastos por período (até 12 meses), levantamento de custos por projeto e ranking dos top 10 itens retirados.
9. **Histórico e Retenção:** Guarda histórico completo com limpeza automática de registros superiores a 18 meses.
---
### TABELAS E DADOS EM TEMPO REAL DO ALMOXARIFADO:
**1. TABELA DE PRODUTOS E ESTOQUE:**
{resumo_produtos}
**2. TABELA DE PREÇOS E NOTAS FISCAIS (COMPRAS):**
{resumo_precos}
**3. ÚLTIMAS RETIRADAS E PROJETOS:**
{resumo_saidas}
---
Sua missão:
- Responder às dúvidas dos usuários com clareza e empatia.
- Ler os dados de preços fornecidos acima para fazer comparativos de custos e sugerir economias.
- Explicar métricas e indicar insights para o BI.
"""
        for msg in st.session_state.chat_history:
            avatar_icon = "🦊" if msg["role"] == "assistant" else "👤"
            with st.chat_message(msg["role"], avatar=avatar_icon):
                st.markdown(msg["content"])
        if prompt := st.chat_input("Pergunte sobre preços, produtos, projetos ou relatórios do BI:"):
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
                
            try:
                conteudo_envio = f"{FOX_SYSTEM_INSTRUCTION_DINAMICO}\n\nPergunta do usuário: {prompt}"
                response = client_gemini.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=conteudo_envio,
                )
                
                resposta = response.text
                st.session_state.chat_history.append({"role": "assistant", "content": resposta})
                with st.chat_message("assistant", avatar="🦊"):
                    st.markdown(resposta)
            except Exception as e:
                st.error(f"Erro ao conversar com a Fox Assistente: {e}")
