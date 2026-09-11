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
            
            # Garantir colunas adicionais no historico
            for col in [("quem_retirou", "TEXT DEFAULT ''"), ("projeto_destino", "TEXT DEFAULT ''")]:
                try:
                    cursor.execute(f"ALTER TABLE historico ADD COLUMN IF NOT EXISTS {col[0]} {col[1]};")
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

            # Zerar informações de quem retirou/projeto após 18 meses (540 dias)
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

if 'banco_inicializado' not in st.session_state:
    inicializar_banco()
    st.session_state.banco_inicializado = True

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

@st.cache_data(ttl=15)
def buscar_produtos():
    conn = conectar()
    return pd.read_sql_query("SELECT id, nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, foto_path, codigo_barras, ca, qtd_minima FROM produtos ORDER BY id ASC", conn)

@st.cache_data(ttl=15)
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
    SELECT h.id, p.nome as produto, p.categoria, h.tipo, h.quantidade, h.usuario, h.responsavel_epi, h.quem_retirou, h.projeto_destino, h.data_hora
    FROM historico h LEFT JOIN produtos p ON h.produto_id = p.id ORDER BY h.id DESC
    """, conn)

# --- CONTEXTO EM TEMPO REAL PARA A FOX ASSISTENTE ---
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

# --- MENU DE NAVEGAÇÃO ---
if st.session_state.perfil == "Operador":
    opcoes_menu = [
        "📦 Consulta de Estoque",
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Projetos & Equipes",
        "➕ Cadastrar Produto",
        "🔄 Retirada / Devolução de Materiais",
        "🦊 Fox Assistente"
    ]
else:
    opcoes_menu = [
        "📦 Consulta de Estoque",
        "🛒 Pedidos de Compras (Itens Faltantes)",
        "🏗️ Projetos & Equipes",
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
        
        def formatar_saldo(row):
            medida = row['unidade_medida']
            if medida == 'Caixa':
                qtd_cx = row['quantidade'] / row['qtd_por_caixa'] if row['qtd_por_caixa'] > 0 else 0
                return f"{row['quantidade']:.0f} un ({qtd_cx:.2f} CX)"
            elif medida == 'Metro':
                return f"{row['quantidade']:.2f} Mts"
            else:
                return f"{row['quantidade']:.0f} Unidades"
                
        df_prod['Saldo Formatado'] = df_prod.apply(formatar_saldo, axis=1)
        
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
            st.download_button("📊 Exportar Excel", data=gerar_excel_download(df_prod), file_name="estoque_atual.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# --- ABA DE PEDIDOS DE COMPRAS ---
elif "Pedidos de Compras" in opcao:
    st.title("🛒 Gerador de Pedidos de Compras & Itens Faltantes")
    df_prod = buscar_produtos()
    if df_prod.empty:
        st.info("Nenhum produto cadastrado.")
    else:
        df_faltantes = df_prod[df_prod['quantidade'] <= df_prod['qtd_minima']].copy()
        if df_faltantes.empty:
            st.success("✅ Todos os produtos estão com níveis normais de estoque!")
        else:
            df_faltantes['Status'] = df_faltantes['quantidade'].apply(lambda x: "ZERADO" if x <= 0 else "REPOR")
            df_faltantes['Qtd a Comprar (Sugestão)'] = df_faltantes.apply(lambda x: max(0.0, float(x['qtd_minima']) - float(x['quantidade'])), axis=1)
            st.dataframe(df_faltantes[['id', 'nome', 'categoria', 'localizacao', 'quantidade', 'qtd_minima', 'Qtd a Comprar (Sugestão)', 'unidade_medida', 'Status']], use_container_width=True)

# --- ABA NOVA: PROJETOS E EQUIPES ---
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
            p_nome = st.text_input("🏗️ Nome do Projeto *", placeholder="Ex: Reforma Galpão B / Obra Cliente X")
            p_desc = st.text_area("📝 Descrição do Projeto", placeholder="Breve detalhamento das atividades...")
            p_equipe = st.text_area("👥 Colaboradores / Equipe Atribuída", placeholder="Ex: João Silva, Maria Souza, Pedro Alves")
            if st.form_submit_button("💾 Cadastrar Projeto", type="primary"):
                if p_nome.strip():
                    ok, msg = cadastrar_projeto(p_nome, p_desc, p_equipe)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Preencha o nome do projeto.")

# --- ABA: RETIRADA E DEVOLUÇÃO COM PROJETO E QUEM RETIROU ---
elif "Retirada / Devolução" in opcao:
    st.title("🔄 Retirada / Devolução de Materiais")
    df_prod = buscar_produtos()
    df_proj = buscar_projetos()
    
    lista_projetos = df_proj['nome_projeto'].tolist() if not df_proj.empty else ["Uso Geral / Manutenção Interna"]
    
    if df_prod.empty:
        st.info("Nenhum produto cadastrado.")
    else:
        st.subheader("1. Tipo de Operação")
        tipo_operacao = st.radio("Selecione a Ação:", ["📤 Retirada (Saída)", "📥 Devolução (Entrada/Reinserção)"], horizontal=True)
        tipo_mov_banco = "SAÍDA" if "Retirada" in tipo_operacao else "DEVOLUÇÃO"
        
        st.write("---")
        st.subheader("2. Selecionar Produto")
        prod_selecionado = st.selectbox("Escolha o Item na Lista:", df_prod['nome'].tolist())
        row = df_prod[df_prod['nome'] == prod_selecionado].iloc[0]
        
        st.write("---")
        st.subheader(f"3. Detalhes da {tipo_operacao}")
        col1, col2 = st.columns(2)
        with col1:
            st.info(f"📦 **Item:** {row['nome']} | **Estoque Atual:** {row['quantidade']} ({row['unidade_medida']})")
            qtd_mov_final = st.number_input(f"Quantidade ({row['unidade_medida']}):", min_value=0.1, step=1.0, value=1.0)
            
        with col2:
            if tipo_mov_banco == "SAÍDA":
                quem_retirou = st.text_input("👤 Nome de Quem Retirou *", placeholder="Ex: Carlos Silva")
                projeto_destino = st.selectbox("🏗️ Projeto Destino *", lista_projetos)
            else:
                quem_retirou = st.text_input("👤 Devolvido por:", value="-")
                projeto_destino = "-"
                
            eh_epi = "EPI" in str(row['categoria']).upper() or "EPI" in str(row['nome']).upper()
            responsavel_epi = ""
            if eh_epi:
                responsavel_epi = st.text_input("🛡️ Responsável pelo EPI (Matrícula/Nome):")

        st.write("---")
        if st.button("Confirmar Movimentação", type="primary"):
            if tipo_mov_banco == "SAÍDA" and not quem_retirou.strip():
                st.error("❌ Por favor, informe o nome de quem está retirando o material!")
            else:
                ok, msg = movimentar_produto(
                    int(row['id']), tipo_mov_banco, float(qtd_mov_final), float(row['quantidade']),
                    st.session_state.usuario, responsavel_epi, quem_retirou, projeto_destino
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

# --- ABA CADASTRO DE PRODUTO ---
elif "Cadastrar Produto" in opcao:
    st.title("➕ Cadastrar Novo Produto")
    with st.form("form_cad_prod", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            nome = st.text_input("📦 Nome do Produto *")
            codigo_barras = st.text_input("🔍 Código de Barras")
            categoria = st.text_input("🏷️ Categoria", value="Geral")
            ca = st.text_input("🛡️ C.A. (EPI)")
            localizacao = st.text_input("📍 Localização", value="Almoxarifado Principal")
        with c2:
            qtd_minima = st.number_input("🔔 Quantidade Mínima", min_value=0.0, value=5.0)
            unidade_medida = st.selectbox("📏 Unidade", ["Caixa", "Metro", "Pacote", "Unidade"])
            qtd_por_caixa = st.number_input("📦 Qtd por Caixa", min_value=1, value=1) if unidade_medida == "Caixa" else 1
            quantidade = st.number_input("📊 Quantidade Inicial", min_value=0.0, value=0.0)
        if st.form_submit_button("➕ Cadastrar Produto"):
            if nome.strip():
                ok, msg = cadastrar_produto(nome, categoria, localizacao, quantidade, unidade_medida, qtd_por_caixa, "", codigo_barras, ca, qtd_minima)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

# --- DEMAIS ABAS PERMANECEM COM LÓGICA E BI EXPANDIDO ---
elif "Dashboard Analytics" in opcao:
    st.title("📊 BI Dashboard - Indicadores Avançados")
    df_prod = buscar_produtos()
    df_hist = buscar_historico()
    df_nf = buscar_notas_fiscais()
    
    st.subheader("🏗️ Análise por Projeto (Últimos 7 dias e Levantamento Geral)")
    if not df_hist.empty:
        df_hist['data_hora'] = pd.to_datetime(df_hist['data_hora'])
        
        # Filtro últimos 7 dias
        data_limite_7d = datetime.now() - timedelta(days=7)
        df_7d = df_hist[(df_hist['tipo'] == 'SAÍDA') & (df_hist['data_hora'] >= data_limite_7d)]
        
        st.markdown("##### 📅 Movimentações de Projetos nos Últimos 7 Dias")
        if not df_7d.empty:
            proj_7d = df_7d.groupby('projeto_destino')['quantidade'].sum().reset_index()
            st.bar_chart(proj_7d.set_index('projeto_destino'))
            st.dataframe(df_7d[['data_hora', 'produto', 'quantidade', 'quem_retirou', 'projeto_destino']], use_container_width=True)
        else:
            st.info("Nenhuma retirada vinculada a projetos nos últimos 7 dias.")
            
        st.write("---")
        st.markdown("##### 🔎 Levantamento Geral de Consumo por Projeto")
        projetos_unicos = df_hist['projeto_destino'].unique().tolist()
        proj_sel = st.selectbox("Selecione o Projeto para Análise:", [p for p in projetos_unicos if p and p != '-'])
        if proj_sel:
            df_proj_det = df_hist[(df_hist['tipo'] == 'SAÍDA') & (df_hist['projeto_destino'] == proj_sel)]
            if not df_proj_det.empty:
                st.write(f"Consumo Total do Projeto: **{df_proj_det['quantidade'].sum()} itens**")
                st.dataframe(df_proj_det[['data_hora', 'produto', 'quantidade', 'quem_retirou', 'usuario']], use_container_width=True)

elif "Histórico / Auditoria" in opcao:
    st.title("📜 Histórico e Auditoria")
    df_hist = buscar_historico()
    st.dataframe(df_hist, use_container_width=True)

# --- ABA FOX ASSISTENTE COM MODELO ATUALIZADO E DADOS DO BANCO ---
elif "Fox Assistente" in opcao:
    st.title("🦊 Fox Assistente - Almoxarifado Inteligente")
    st.caption("Sua assistente integrada com leitura em tempo real do banco de dados, preços e estoque!")
    
    if not client_gemini:
        st.error("Chave de API do Gemini não configurada em .streamlit/secrets.toml.")
    else:
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []
            
        for msg in st.session_state.chat_history:
            avatar_icon = "🦊" if msg["role"] == "assistant" else "👤"
            with st.chat_message(msg["role"], avatar=avatar_icon):
                st.markdown(msg["content"])
                
        if prompt := st.chat_input("Pergunte sobre estoque, preços de NF, projetos ou ajuda no sistema:"):
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
                
            try:
                contexto_banco = gerar_contexto_dados_sistema()
                instrucao_completa = f"{contexto_banco}\n\nVocê é a Fox Assistente 🦊. Responda à dúvida do usuário utilizando as informações fornecidas do banco de dados (estoque, preços, NFs e projetos) e forneça insights, comparativos de preços de compras ou orientações operacionais.\n\nPergunta: {prompt}"
                
                # MODELO ATUALIZADO PARA gemini-3.6-flash
                response = client_gemini.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=instrucao_completa,
                )
                resposta = response.text
                st.session_state.chat_history.append({"role": "assistant", "content": resposta})
                with st.chat_message("assistant", avatar="🦊"):
                    st.markdown(resposta)
            except Exception as e:
                st.error(f"Erro ao conversar com a Fox Assistente: {e}")
