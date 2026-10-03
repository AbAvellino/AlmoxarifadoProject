import streamlit as st
import pandas as pd
import json
import os
import hashlib
from datetime import datetime, timedelta
import io
import gspread
from google.oauth2.service_account import Credentials
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from concurrent.futures import ThreadPoolExecutor

# ==============================================================================
# CONFIGURAÇÃO DE PÁGINA E ESTILOS CSS
# ==============================================================================
st.set_page_config(
    page_title="Central Unificada de Planilhas - Almoxarifado",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown("""
    <style>
        .block-container { 
            padding-top: 1rem !important; 
            padding-bottom: 1rem !important; 
            padding-left: 1.5rem !important; 
            padding-right: 1.5rem !important; 
        }
        #MainMenu, footer, header { visibility: hidden; }
        .stButton>button { width: 100%; border-radius: 8px; font-weight: 600; }
        .log-box { font-family: monospace; font-size: 12px; background-color: #1e1e2f; padding: 10px; border-radius: 5px; }
        .card-stat { background-color: #1e293b; padding: 15px; border-radius: 10px; border: 1px solid #334155; }
    </style>
""", unsafe_allow_html=True)

# ==============================================================================
# FUNÇÕES DE CRIPTOGRAFIA
# ==============================================================================
def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

def verificar_senha(senha_input: str, senha_hash: str) -> bool:
    return hash_senha(senha_input) == senha_hash

# ==============================================================================
# POOL DE CONEXÕES POSTGRESQL
# ==============================================================================
@st.cache_resource
def iniciar_db_pool():
    try:
        db_url = st.secrets["postgres"]["url"]
        return pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=db_url)
    except Exception as e:
        st.error(f"⚠️ Erro ao criar pool do Banco de Dados: {e}")
        return None

db_pool = iniciar_db_pool()

def executar_query(query, params=None, fetch="none"):
    if not db_pool:
        return None
    conn = db_pool.getconn()
    try:
        cursor_factory = RealDictCursor if fetch in ["one", "all"] else None
        with conn.cursor(cursor_factory=cursor_factory) as cursor:
            cursor.execute(query, params or ())
            res = None
            if fetch == "one":
                res = cursor.fetchone()
            elif fetch == "all":
                res = cursor.fetchall()
            conn.commit()
            return res
    except Exception as e:
        conn.rollback()
        st.error(f"Erro no banco de dados: {e}")
        return None
    finally:
        db_pool.putconn(conn)

def inicializar_banco():
    executar_query("""
        CREATE TABLE IF NOT EXISTS usuarios (
            login VARCHAR(50) PRIMARY KEY,
            senha VARCHAR(128) NOT NULL,
            nome VARCHAR(100) NOT NULL,
            setores TEXT[] NOT NULL,
            permissao VARCHAR(20) NOT NULL,
            e_admin BOOLEAN DEFAULT FALSE
        );
    """)
    executar_query("""
        CREATE TABLE IF NOT EXISTS planilhas (
            id SERIAL PRIMARY KEY,
            setor VARCHAR(50) NOT NULL,
            nome VARCHAR(100) NOT NULL,
            spreadsheet_id VARCHAR(128) NOT NULL,
            UNIQUE(setor, nome)
        );
    """)
    executar_query("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            usuario VARCHAR(50) NOT NULL,
            acao VARCHAR(100) NOT NULL,
            detalhe TEXT
        );
    """)
    
    # Novas tabelas para suporte às 4 funcionalidades requisitadas
    executar_query("""
        CREATE TABLE IF NOT EXISTS projetos (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL UNIQUE,
            descricao TEXT,
            status VARCHAR(30) DEFAULT 'Ativo'
        );
    """)
    executar_query("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            cargo VARCHAR(100) NOT NULL,
            projeto_id INT REFERENCES projetos(id) ON DELETE SET NULL,
            status VARCHAR(30) DEFAULT 'Ativo'
        );
    """)
    executar_query("""
        CREATE TABLE IF NOT EXISTS ferramentas_checklist (
            id SERIAL PRIMARY KEY,
            codigo VARCHAR(50) UNIQUE NOT NULL,
            nome VARCHAR(100) NOT NULL,
            categoria VARCHAR(50),
            responsavel_atual VARCHAR(100) DEFAULT 'Almoxarifado',
            estado VARCHAR(50) DEFAULT 'Bom',
            ultima_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    executar_query("""
        CREATE TABLE IF NOT EXISTS movimentacoes_estoque (
            id SERIAL PRIMARY KEY,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tipo VARCHAR(20) NOT NULL, -- 'Entrada' ou 'Saida'
            item VARCHAR(100) NOT NULL,
            quantidade INT NOT NULL,
            valor_unitario NUMERIC(10,2) DEFAULT 0.00,
            valor_total NUMERIC(10,2) DEFAULT 0.00,
            projeto VARCHAR(100),
            solicitante VARCHAR(100),
            usuario VARCHAR(50)
        );
    """)

    # Inserção de usuário padrão admin e planilhas base
    executar_query("""
        INSERT INTO usuarios (login, senha, nome, setores, permissao, e_admin)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (login) DO NOTHING;
    """, ("admin", hash_senha("123"), "Gerência / Admin", ["Visão Geral", "Busca Global", "Almoxarifado", "Containers", "Checklist Ferramentas", "Equipe & Projetos", "Retirada Múltipla", "Dashboard Analytics", "Painel Admin"], "Escrita", True))

    executar_query("""
        INSERT INTO planilhas (setor, nome, spreadsheet_id) VALUES
        ('Almoxarifado', 'Controle', '1nb-gVt6e98Kh4BAYl9l-dgspleRHZDfe8DAT2B1OB_I'),
        ('Almoxarifado', 'Ferro Quantidade', '1kyrYqJoJLyaL8fvCFVvnFgAbEHIAv6h1W2ZHt1Hmhn4'),
        ('Containers', 'Controle de containers em patio', '1Im_QMBgD1GYDSe6v4-xvN6rOHUAGTZjA-fl3hB1w5tg')
        ON CONFLICT DO NOTHING;
    """)

inicializar_banco()

@st.cache_data(ttl=300)
def carregar_usuarios():
    rows = executar_query("SELECT * FROM usuarios;", fetch="all")
    if not rows:
        return {}
    return {r["login"]: dict(r) for r in rows}

@st.cache_data(ttl=300)
def carregar_planilhas_por_setor():
    rows = executar_query("SELECT * FROM planilhas;", fetch="all")
    if not rows:
        return {}
    planilhas_dict = {}
    for r in rows:
        setor = r["setor"]
        if setor not in planilhas_dict:
            planilhas_dict[setor] = {}
        planilhas_dict[setor][r["nome"]] = r["spreadsheet_id"]
    return planilhas_dict

def registrar_log(usuario, acao, detalhe):
    executar_query("""
        INSERT INTO logs (usuario, acao, detalhe)
        VALUES (%s, %s, %s);
    """, (usuario, acao, detalhe))

def obter_logs():
    rows = executar_query("SELECT data_hora, usuario, acao, detalhe FROM logs ORDER BY id DESC LIMIT 200;", fetch="all")
    return pd.DataFrame(rows) if rows else pd.DataFrame()

USUARIOS = carregar_usuarios()
PLANILHAS_POR_SETOR = carregar_planilhas_por_setor()

# ==============================================================================
# CONEXÃO API GOOGLE SHEETS
# ==============================================================================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

@st.cache_resource
def conectar_google_api():
    try:
        if "gcp_service_account" in st.secrets:
            credentials_info = dict(st.secrets["gcp_service_account"])
            if "private_key" in credentials_info:
                credentials_info["private_key"] = credentials_info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
            return gspread.authorize(creds)
        elif os.path.exists("chave.json"):
            creds = Credentials.from_service_account_file("chave.json", scopes=SCOPES)
            return gspread.authorize(creds)
        return None
    except Exception as e:
        st.error(f"⚠️ Erro ao conectar na API do Google: {e}")
        return None

client_gspread = conectar_google_api()

@st.cache_data(ttl=300)
def obter_abas_planilha(spreadsheet_id):
    if not client_gspread:
        return []
    try:
        sh = client_gspread.open_by_key(spreadsheet_id)
        return [ws.title for ws in sh.worksheets()]
    except Exception:
        return []

@st.cache_data(ttl=300)
def ler_planilha_api(spreadsheet_id, nome_aba=None):
    if not client_gspread:
        return None
    try:
        sh = client_gspread.open_by_key(spreadsheet_id)
        sheet = sh.worksheet(nome_aba) if nome_aba else sh.sheet1
        dados = sheet.get_all_records()
        return pd.DataFrame(dados)
    except Exception as e:
        st.error(f"Erro ao acessar planilha/aba via API: {e}")
        return None

def salvar_alteracoes_api(spreadsheet_id, df_atualizado, nome_aba=None):
    if not client_gspread:
        return False
    try:
        sh = client_gspread.open_by_key(spreadsheet_id)
        sheet = sh.worksheet(nome_aba) if nome_aba else sh.sheet1
        sheet.clear()
        
        df_limpo = df_atualizado.fillna("").astype(str)
        conteudo = [df_limpo.columns.values.tolist()] + df_limpo.values.tolist()
        
        sheet.update(conteudo)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar na planilha: {e}")
        return False

# ==============================================================================
# CONTROLE DE SESSÃO E LOGIN
# ==============================================================================
if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None

if st.session_state["usuario_logado"] is None:
    st.markdown("<br><br>", unsafe_allow_html=True)
    c1, col_login, c2 = st.columns([1, 1.2, 1])
    
    with col_login:
        st.title("🔒 Central Unificada")
        st.caption("Acesse com suas credenciais seguras.")
        
        with st.form("form_login"):
            usuario = st.text_input("👤 Usuário").strip().lower()
            senha = st.text_input("🔑 Senha", type="password")
            btn_entrar = st.form_submit_button("🚀 Entrar no Sistema")
            
            if btn_entrar:
                with st.spinner("Autenticando..."):
                    if usuario in USUARIOS:
                        senha_armazenada = USUARIOS[usuario]["senha"]
                        if verificar_senha(senha, senha_armazenada):
                            st.session_state["usuario_logado"] = usuario
                            registrar_log(usuario, "Login", "Usuário autenticado")
                            st.rerun()
                        else:
                            st.error("Senha incorreta.")
                    else:
                        st.error("Usuário não encontrado.")
else:
    dados_usuario = USUARIOS.get(st.session_state["usuario_logado"], {})
    setores_permitidos = dados_usuario.get("setores", [])
    
    c_setor, c_planilha, c_modo, c_user = st.columns([1.2, 1.3, 1.5, 0.8])
    
    with c_setor:
        setor_selecionado = st.selectbox("🏢 Setor / Área", setores_permitidos)

    # ==============================================================================
    # 1. CHECKLIST DE FERRAMENTAS (EDIÇÃO RÁPIDA DE COM QUEM ESTÁ)
    # ==============================================================================
    if setor_selecionado == "Checklist Ferramentas":
        with c_planilha:
            st.selectbox("📁 Módulo", ["Gestão de Custódia"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_chk"):
                st.session_state["usuario_logado"] = None
                st.rerun()
                
        st.markdown("---")
        st.subheader("🔧 Checklist & Custódia Rápida de Ferramentas")
        st.caption("Altere a posse e localização das ferramentas cadastradas com alta velocidade.")
        
        tab_list, tab_cad = st.tabs(["📋 Lista & Alteração Rápida", "➕ Cadastrar Nova Ferramenta"])
        
        with tab_list:
            ferramentas = executar_query("SELECT * FROM ferramentas_checklist ORDER BY id ASC;", fetch="all")
            if ferramentas:
                df_f = pd.DataFrame(ferramentas)
                st.markdown("### ⚡ Edição Direta na Tabela")
                
                # Editor Interativo Rápido
                df_editado_f = st.data_editor(
                    df_f,
                    column_config={
                        "id": None,
                        "codigo": "Código / Tag",
                        "nome": "Ferramenta",
                        "categoria": "Categoria",
                        "responsavel_atual": st.column_config.SelectboxColumn("Com quem está? (Responsável)", help="Escolha quem está com a ferramenta", options=["Almoxarifado", "Oficina", "Manutenção"] + [f['nome'] for f in (executar_query("SELECT nome FROM funcionarios;", fetch="all") or [])], required=True),
                        "estado": st.column_config.SelectboxColumn("Estado", options=["Bom", "Em Manutenção", "Danificado", "Perdido"]),
                        "ultima_atualizacao": st.column_config.DatetimeColumn("Última Alteração", disabled=True)
                    },
                    use_container_width=True,
                    key="editor_ferramentas"
                )
                
                if st.button("💾 Salvar Alterações no Checklist"):
                    for idx, row in df_editado_f.iterrows():
                        executar_query("""
                            UPDATE ferramentas_checklist 
                            SET responsavel_atual = %s, estado = %s, ultima_atualizacao = NOW()
                            WHERE id = %s;
                        """, (row['responsavel_atual'], row['estado'], row['id']))
                    registrar_log(st.session_state["usuario_logado"], "Checklist Ferramentas", "Atualização rápida de responsáveis realizada")
                    st.success("Custódia de ferramentas atualizada com sucesso!")
                    st.rerun()
            else:
                st.info("Nenhuma ferramenta cadastrada ainda no checklist.")

        with tab_cad:
            with st.form("form_add_ferramenta"):
                cod = st.text_input("Código/Tag da Ferramenta").strip()
                nome_f = st.text_input("Nome da Ferramenta").strip()
                cat_f = st.text_input("Categoria (ex: Elétrica, Manual, Medição)").strip()
                resp_f = st.text_input("Possuidor Inicial", value="Almoxarifado").strip()
                btn_save_f = st.form_submit_button("➕ Cadastrar Ferramenta")
                if btn_save_f:
                    if cod and nome_f:
                        executar_query("""
                            INSERT INTO ferramentas_checklist (codigo, nome, categoria, responsavel_atual)
                            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;
                        """, (cod, nome_f, cat_f, resp_f))
                        registrar_log(st.session_state["usuario_logado"], "Cadastro Ferramenta", f"Ferramenta {cod} cadastrada")
                        st.success("Ferramenta cadastrada!")
                        st.rerun()

    # ==============================================================================
    # 2. GESTÃO DE PROJETO E EQUIPE (EDITAR CARGOS E EXCLUIR)
    # ==============================================================================
    elif setor_selecionado == "Equipe & Projetos":
        with c_planilha:
            st.selectbox("📁 Módulo", ["Projetos e Colaboradores"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_eq"):
                st.session_state["usuario_logado"] = None
                st.rerun()
                
        st.markdown("---")
        st.subheader("👥 Cadastro e Gestão de Projetos e Equipe")
        
        tab_func, tab_proj = st.tabs(["👨‍🔧 Colaboradores / Funcionários", "🏗️ Projetos"])
        
        with tab_func:
            col_list, col_edit = st.columns([1.5, 1])
            
            with col_list:
                st.markdown("### Lista de Funcionários")
                funcs = executar_query("""
                    SELECT f.id, f.nome, f.cargo, p.nome as projeto, f.status 
                    FROM funcionarios f 
                    LEFT JOIN projetos p ON f.projeto_id = p.id
                    ORDER BY f.id DESC;
                """, fetch="all")
                if funcs:
                    st.dataframe(pd.DataFrame(funcs), use_container_width=True)
                else:
                    st.info("Nenhum funcionário cadastrado.")
                    
            with col_edit:
                st.markdown("### Editar ou Excluir Funcionário")
                if funcs:
                    dict_funcs = {f"{f['id']} - {f['nome']} ({f['cargo']})": f for f in funcs}
                    sel_f = st.selectbox("Selecione o Colaborador:", list(dict_funcs.keys()))
                    f_data = dict_funcs[sel_f]
                    
                    with st.form("form_edit_func"):
                        novo_nome_func = st.text_input("Nome", value=f_data['nome'])
                        novo_cargo_func = st.text_input("Cargo (Editar Cargo)", value=f_data['cargo'])
                        projs_list = executar_query("SELECT id, nome FROM projetos;", fetch="all") or []
                        dict_proj_opts = {p['nome']: p['id'] for p in projs_list}
                        
                        proj_atual_nome = f_data['projeto'] if f_data['projeto'] in dict_proj_opts else (list(dict_proj_opts.keys())[0] if dict_proj_opts else None)
                        
                        sel_proj_f = st.selectbox("Projeto Vinculado:", list(dict_proj_opts.keys()) if dict_proj_opts else ["Nenhum"], index=list(dict_proj_opts.keys()).index(proj_atual_nome) if proj_atual_nome in dict_proj_opts else 0)
                        
                        btn_up_func = st.form_submit_button("💾 Salvar Alterações de Cargo/Dados")
                        
                        if btn_up_func:
                            p_id = dict_proj_opts.get(sel_proj_f)
                            executar_query("""
                                UPDATE funcionarios SET nome=%s, cargo=%s, projeto_id=%s WHERE id=%s;
                            """, (novo_nome_func, novo_cargo_func, p_id, f_data['id']))
                            registrar_log(st.session_state["usuario_logado"], "Alteração Funcionário", f"Funcionário ID {f_data['id']} atualizado")
                            st.success("Dados atualizados!")
                            st.rerun()
                    
                    if st.button("❌ Excluir Funcionário", key="btn_del_func_item"):
                        executar_query("DELETE FROM funcionarios WHERE id=%s;", (f_data['id'],))
                        registrar_log(st.session_state["usuario_logado"], "Exclusão Funcionário", f"Funcionário {f_data['nome']} removido")
                        st.warning("Funcionário excluído do cadastro!")
                        st.rerun()

                st.markdown("---")
                st.markdown("### ➕ Novo Colaborador")
                with st.form("form_add_func"):
                    add_n_f = st.text_input("Nome Completo")
                    add_c_f = st.text_input("Cargo")
                    btn_add_f = st.form_submit_button("➕ Cadastrar Colaborador")
                    if btn_add_f and add_n_f:
                        executar_query("INSERT INTO funcionarios (nome, cargo) VALUES (%s, %s);", (add_n_f, add_c_f))
                        st.success("Colaborador Cadastrado!")
                        st.rerun()

        with tab_proj:
            st.markdown("### Cadastro e Gestão de Projetos")
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                projs = executar_query("SELECT * FROM projetos ORDER BY id DESC;", fetch="all")
                if projs:
                    st.dataframe(pd.DataFrame(projs), use_container_width=True)
            with c_p2:
                with st.form("form_add_proj"):
                    n_p = st.text_input("Nome do Projeto/Obra")
                    d_p = st.text_area("Descrição")
                    if st.form_submit_button("➕ Cadastrar Projeto") and n_p:
                        executar_query("INSERT INTO projetos (nome, descricao) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (n_p, d_p))
                        st.success("Projeto criado!")
                        st.rerun()

    # ==============================================================================
    # 3. RETIRADA DE ITENS (MÚLTIPLOS ITENS POR VEZ)
    # ==============================================================================
    elif setor_selecionado == "Retirada Múltipla":
        with c_planilha:
            st.selectbox("📁 Módulo", ["Saída de Almoxarifado"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_ret"):
                st.session_state["usuario_logado"] = None
                st.rerun()
                
        st.markdown("---")
        st.subheader("📦 Retirada Múltipla de Itens (Carrinho de Saída)")
        st.caption("Selecione múltiplos itens, quantidades e confirme a retirada de uma só vez.")
        
        if "carrinho_retirada" not in st.session_state:
            st.session_state["carrinho_retirada"] = []
            
        projs_list = executar_query("SELECT nome FROM projetos;", fetch="all") or []
        funcs_list = executar_query("SELECT nome FROM funcionarios;", fetch="all") or []
        
        c_proj, c_solic = st.columns(2)
        with c_proj:
            proj_saida = st.selectbox("Projeto Destino:", [p['nome'] for p in projs_list] if projs_list else ["Geral / Sem Projeto"])
        with c_solic:
            solic_saida = st.selectbox("Solicitante / Retirado por:", [f['nome'] for f in funcs_list] if funcs_list else ["Almoxarife"])
            
        st.markdown("### Add Itens ao Carrinho")
        col_item, col_qtd, col_vlr, col_btn = st.columns([3, 1, 1, 1])
        
        with col_item:
            item_nome = st.text_input("Nome / Descrição do Item").strip()
        with col_qtd:
            item_qtd = st.number_input("Qtd", min_value=1, value=1)
        with col_vlr:
            item_vlr = st.number_input("Valor Un. (R$)", min_value=0.0, value=0.0, step=0.50)
        with col_btn:
            st.write("<br>", unsafe_allow_html=True)
            if st.button("➕ Adicionar"):
                if item_nome:
                    st.session_state["carrinho_retirada"].append({
                        "item": item_nome,
                        "quantidade": item_qtd,
                        "valor_unitario": item_vlr,
                        "valor_total": item_qtd * item_vlr
                    })
                    st.success(f"Item '{item_nome}' adicionado!")
                    st.rerun()
                    
        if st.session_state["carrinho_retirada"]:
            st.markdown("---")
            st.markdown("### 🛒 Itens no Carrinho para Retirada")
            df_carrinho = pd.DataFrame(st.session_state["carrinho_retirada"])
            st.dataframe(df_carrinho, use_container_width=True)
            
            c_conf, c_limp = st.columns([2, 1])
            with c_conf:
                if st.button("✅ CONFIRMAR RETIRADA DE TODOS OS ITENS"):
                    for row in st.session_state["carrinho_retirada"]:
                        executar_query("""
                            INSERT INTO movimentacoes_estoque 
                            (tipo, item, quantidade, valor_unitario, valor_total, projeto, solicitante, usuario)
                            VALUES ('Saida', %s, %s, %s, %s, %s, %s, %s);
                        """, (row['item'], row['quantidade'], row['valor_unitario'], row['valor_total'], proj_saida, solic_saida, st.session_state["usuario_logado"]))
                    
                    registrar_log(st.session_state["usuario_logado"], "Retirada Múltipla", f"Retirados {len(st.session_state['carrinho_retirada'])} itens para o projeto {proj_saida}")
                    st.session_state["carrinho_retirada"] = []
                    st.balloons()
                    st.success("Retirada múltipla concluída e registrada com sucesso!")
                    st.rerun()
            with c_limp:
                if st.button("🗑️ Limpar Carrinho"):
                    st.session_state["carrinho_retirada"] = []
                    st.rerun()

    # ==============================================================================
    # 4. DASHBOARD ANALYTICS (VALORES POR PROJETO E INTERVALOS TEMPORAIS)
    # ==============================================================================
    elif setor_selecionado == "Dashboard Analytics":
        with c_planilha:
            st.selectbox("📁 Módulo", ["Métricas e Analytics"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_dash"):
                st.session_state["usuario_logado"] = None
                st.rerun()
                
        st.markdown("---")
        st.subheader("📊 Dashboard Analytics do Almoxarifado")
        
        # Filtros Temporais
        col_filtro, col_tipo = st.columns([2, 2])
        with col_filtro:
            periodo = st.selectbox("📅 Período de Análise:", ["Hoje", "Semana (Últimos 7 dias)", "Mês (Últimos 30 dias)", "Seis Meses (180 dias)", "Ano (365 dias)"])
        with col_tipo:
            tipo_mov = st.selectbox("🔄 Tipo de Movimentação:", ["Todas", "Entrada", "Saida"])
            
        dias_map = {"Hoje": 0, "Semana (Últimos 7 dias)": 7, "Mês (Últimos 30 dias)": 30, "Seis Meses (180 dias)": 180, "Ano (365 dias)": 365}
        dias = dias_map[periodo]
        
        query_dash = "SELECT * FROM movimentacoes_estoque WHERE 1=1 "
        params_dash = []
        
        if dias == 0:
            query_dash += " AND DATE(data_hora) = CURRENT_DATE"
        else:
            query_dash += f" AND data_hora >= CURRENT_DATE - INTERVAL '{dias} days'"
            
        if tipo_mov != "Todas":
            query_dash += " AND tipo = %s"
            params_dash.append(tipo_mov)
            
        rows_dash = executar_query(query_dash, params_dash, fetch="all")
        
        if rows_dash:
            df_dash = pd.DataFrame(rows_dash)
            
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Total Movimentado (R$)", f"R$ {df_dash['valor_total'].sum():,.2f}")
            with m2:
                st.metric("Qtd Total de Itens", f"{df_dash['quantidade'].sum():,} un")
            with m3:
                st.metric("Nº de Operações", f"{len(df_dash)}")
                
            st.markdown("---")
            c_g1, c_g2 = st.columns(2)
            
            with c_g1:
                st.markdown("### 🏗️ Valores Acumulados por Projeto")
                df_proj = df_dash.groupby("projeto")["valor_total"].sum().reset_index()
                st.bar_chart(df_proj.set_index("projeto"))
                
            with c_g2:
                st.markdown("### 📈 Evolução dos Valores no Período")
                df_dash['data'] = pd.to_datetime(df_dash['data_hora']).dt.date
                df_tempo = df_dash.groupby("data")["valor_total"].sum().reset_index()
                st.line_chart(df_tempo.set_index("data"))
                
            st.markdown("---")
            st.markdown("### 📄 Detalhamento das Movimentações Registradas")
            st.dataframe(df_dash, use_container_width=True)
            
        else:
            st.info("Nenhuma movimentação ou valor registrado para os filtros selecionados.")

    # --- PAINEL ADMIN E LOGS ---
    elif setor_selecionado == "Painel Admin":
        if not dados_usuario.get("e_admin", False):
            st.error("🚫 Acesso não autorizado. Apenas administradores possuem acesso a este painel.")
            st.stop()
        with c_planilha:
            st.selectbox("📁 Planilha", ["Gestão do Sistema"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_admin"):
                st.session_state["usuario_logado"] = None
                st.rerun()
        st.markdown("---")
        st.subheader("⚙️ Painel do Administrador & Logs de Auditoria")
        tab_planilhas, tab_cadastrar_usr, tab_gerenciar_usr, tab_logs = st.tabs([
            "➕ Cadastrar Planilha", 
            "👤 Cadastrar Novo Usuário", 
            "📋 Gerenciar / Alterar / Excluir Usuários",
            "📜 Logs de Auditoria"
        ])
        with tab_planilhas:
            st.markdown("### Cadastrar Nova Planilha")
            setores_existentes = list(PLANILHAS_POR_SETOR.keys())
            novo_setor_check = st.checkbox("Criar um novo setor")
            
            if novo_setor_check:
                setor_dest = st.text_input("Nome do Novo Setor").strip()
            else:
                setor_dest = st.selectbox("Selecionar Setor Existente", setores_existentes) if setores_existentes else st.text_input("Nome do Setor").strip()
                
            nome_planilha = st.text_input("Nome da Planilha").strip()
            id_planilha_input = st.text_input("ID do Google Sheets").strip()
            if st.button("💾 Salvar Planilha no Banco"):
                if setor_dest and nome_planilha and id_planilha_input:
                    executar_query("""
                        INSERT INTO planilhas (setor, nome, spreadsheet_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (setor, nome) DO UPDATE SET spreadsheet_id = EXCLUDED.spreadsheet_id;
                    """, (setor_dest, nome_planilha, id_planilha_input))
                    
                    registrar_log(st.session_state["usuario_logado"], "Cadastro Planilha", f"Planilha '{nome_planilha}' no setor '{setor_dest}'")
                    st.cache_data.clear()
                    st.success("Planilha gravada com sucesso!")
                    st.rerun()
                else:
                    st.warning("Preencha todos os campos para salvar.")
        with tab_cadastrar_usr:
            st.markdown("### Cadastrar Novo Usuário")
            novo_login = st.text_input("Login (ex: joao)").strip().lower()
            nova_senha = st.text_input("Senha Inicial", type="password").strip()
            nome_completo = st.text_input("Nome Exibido").strip()
            
            todos_setores = list(set(["Visão Geral", "Busca Global", "Painel Admin", "Checklist Ferramentas", "Equipe & Projetos", "Retirada Múltipla", "Dashboard Analytics"] + list(PLANILHAS_POR_SETOR.keys())))
            setores_usuario = st.multiselect("Setores Permitidos:", todos_setores, default=["Visão Geral", "Busca Global"])
            permissao_tipo = st.radio("Nível de Acesso às Planilhas:", ["Escrita", "Leitura"], horizontal=True)
            e_admin_check = st.checkbox("Tornar Administrador do Sistema")
            if st.button("👤 Salvar Usuário no Banco"):
                if novo_login and nova_senha and nome_completo and setores_usuario:
                    if novo_login in USUARIOS:
                        st.error(f"O login '{novo_login}' já está cadastrado.")
                    else:
                        executar_query("""
                            INSERT INTO usuarios (login, senha, nome, setores, permissao, e_admin)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (login) DO UPDATE SET 
                                senha = EXCLUDED.senha,
                                nome = EXCLUDED.nome,
                                setores = EXCLUDED.setores,
                                permissao = EXCLUDED.permissao,
                                e_admin = EXCLUDED.e_admin;
                        """, (novo_login, hash_senha(nova_senha), nome_completo, setores_usuario, permissao_tipo, e_admin_check))
                        registrar_log(st.session_state["usuario_logado"], "Cadastro Usuário", f"Usuário '{novo_login}' criado.")
                        st.cache_data.clear()
                        st.success(f"Usuário '{novo_login}' criado!")
                        st.rerun()
                else:
                    st.warning("Preencha todos os campos obrigatórios.")
        with tab_gerenciar_usr:
            st.markdown("### Gerenciar Usuários no Banco")
            todos_setores = list(set(["Visão Geral", "Busca Global", "Painel Admin", "Checklist Ferramentas", "Equipe & Projetos", "Retirada Múltipla", "Dashboard Analytics"] + list(PLANILHAS_POR_SETOR.keys())))
            lista_logins = list(USUARIOS.keys())
            if lista_logins:
                usr_sel = st.selectbox("Selecione o Usuário para Alterar/Excluir:", lista_logins)
                info = USUARIOS[usr_sel]
                c_n, c_s = st.columns(2)
                with c_n:
                    novo_nome_val = st.text_input("Nome do Usuário", value=info["nome"], key=f"nome_{usr_sel}")
                with c_s:
                    nova_senha_val = st.text_input("Nova Senha (deixe em branco para manter)", type="password", key=f"pwd_{usr_sel}")
                c_perm1, c_perm2, c_admin = st.columns([2, 1, 1])
                with c_perm1:
                    novos_setores = st.multiselect("Setores liberados:", options=todos_setores, default=info.get("setores", []), key=f"ms_{usr_sel}")
                with c_perm2:
                    nova_perm = st.selectbox("Modo de Acesso:", ["Escrita", "Leitura"], index=0 if info.get("permissao","Escrita") == "Escrita" else 1, key=f"perm_{usr_sel}")
                with c_admin:
                    e_admin_val = st.checkbox("É Admin", value=info.get("e_admin", False), key=f"chk_admin_{usr_sel}")
                st.markdown("---")
                c_btn_save, c_btn_del = st.columns([2, 1])
                
                with c_btn_save:
                    if st.button("💾 Salvar Alterações", key=f"btn_up_{usr_sel}"):
                        if nova_senha_val.strip():
                            executar_query("""
                                UPDATE usuarios SET nome=%s, senha=%s, setores=%s, permissao=%s, e_admin=%s WHERE login=%s;
                            """, (novo_nome_val, hash_senha(nova_senha_val.strip()), novos_setores, nova_perm, e_admin_val, usr_sel))
                        else:
                            executar_query("""
                                UPDATE usuarios SET nome=%s, setores=%s, permissao=%s, e_admin=%s WHERE login=%s;
                            """, (novo_nome_val, novos_setores, nova_perm, e_admin_val, usr_sel))
                        registrar_log(st.session_state["usuario_logado"], "Alteração Usuário", f"Usuário '{usr_sel}' atualizado")
                        st.cache_data.clear()
                        st.success(f"Usuário '{usr_sel}' atualizado!")
                        st.rerun()
                
                with c_btn_del:
                    if st.button("❌ Excluir Usuário", key=f"btn_del_{usr_sel}"):
                        if usr_sel == st.session_state["usuario_logado"]:
                            st.error("Você não pode excluir a sua própria conta logada!")
                        else:
                            executar_query("DELETE FROM usuarios WHERE login=%s;", (usr_sel,))
                            registrar_log(st.session_state["usuario_logado"], "Exclusão Usuário", f"Usuário '{usr_sel}' removido")
                            st.cache_data.clear()
                            st.warning(f"Usuário '{usr_sel}' removido!")
                            st.rerun()
        with tab_logs:
            st.markdown("### 📜 Histórico de Atividades / Auditoria")
            df_logs = obter_logs()
            if not df_logs.empty:
                st.dataframe(df_logs, use_container_width=True)
            else:
                st.info("Nenhum log registrado ainda.")

    # --- BUSCA GLOBAL ---
    elif setor_selecionado == "Busca Global":
        with c_planilha:
            st.selectbox("📁 Planilha", ["Varredura Multisetor"], disabled=True)
        with c_modo:
            st.empty()
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout_search"):
                st.session_state["usuario_logado"] = None
                st.rerun()
        st.markdown("---")
        st.subheader("🔍 Busca Global em Todas as Planilhas")
        termo_busca = st.text_input("Digite o código, nome de item, ID de container ou palavra-chave:")
        
        if st.button("🔎 Pesquisar no Sistema") and termo_busca:
            encontrados = 0
            
            def buscar_na_planilha(args):
                setor, nome_plan, sheet_id = args
                df = ler_planilha_api(sheet_id)
                if df is not None and not df.empty:
                    mascara = df.astype(str).apply(lambda row: row.str.contains(termo_busca, case=False, na=False)).any(axis=1)
                    res = df[mascara]
                    if not res.empty:
                        return setor, nome_plan, res
                return None
            tarefas = []
            for setor, planilhas in PLANILHAS_POR_SETOR.items():
                for nome_plan, sheet_id in planilhas.items():
                    tarefas.append((setor, nome_plan, sheet_id))
            if tarefas:
                with st.spinner("Pesquisando em paralelo..."):
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        resultados_pesquisa = list(executor.map(buscar_na_planilha, tarefas))
                for item in resultados_pesquisa:
                    if item:
                        setor, nome_plan, df_res = item
                        encontrados += len(df_res)
                        st.write(f"📍 **Setor:** `{setor}` | **Planilha:** `{nome_plan}` ({len(df_res)} registro(s))")
                        st.dataframe(df_res, use_container_width=True)
            if encontrados == 0:
                st.warning("Nenhum resultado encontrado para o termo pesquisado.")

    # --- DEMAIS SETORES E OPERAÇÃO NATIVA GOOGLE SHEETS ---
    else:
        planilhas_do_setor = PLANILHAS_POR_SETOR.get(setor_selecionado, {})
        with c_planilha:
            if planilhas_do_setor:
                planilha_selecionada = st.selectbox("📁 Planilha", list(planilhas_do_setor.keys()))
                id_planilha = planilhas_do_setor[planilha_selecionada]
            else:
                st.selectbox("📁 Planilha", ["Nenhuma planilha cadastrada"], disabled=True)
                id_planilha = None
            
        with c_modo:
            modo_visualizacao = st.radio(
                "🖥️ Modo de Exibição", 
                ["🌐 Google Sheets Oficial (Completo)", "⚡ Tabela Nativa (API Rápida)"],
                horizontal=True
            )
        with c_user:
            st.write(f"👤 **{dados_usuario.get('nome','')}**")
            if st.button("🚪 Sair", key="btn_logout"):
                st.session_state["usuario_logado"] = None
                st.rerun()
        st.markdown("---")
        if id_planilha:
            if modo_visualizacao == "🌐 Google Sheets Oficial (Completo)":
                embed_url = f"https://docs.google.com/spreadsheets/d/{id_planilha}/edit"
                st.components.v1.html(
                    f'<iframe src="{embed_url}" width="100%" height="750" frameborder="0" style="border:1px solid #333; border-radius:8px;"></iframe>',
                    height=755
                )
            else:
                abas = obter_abas_planilha(id_planilha)
                aba_selecionada = st.selectbox("📑 Selecione a Aba da Planilha:", abas) if abas else None
                df_dados = ler_planilha_api(id_planilha, aba_selecionada)
                
                if df_dados is not None:
                    pode_editar = (dados_usuario.get("permissao", "Escrita") == "Escrita")
                    
                    editor_key = f"editor_{id_planilha}_{aba_selecionada}"
                    
                    df_editado = st.data_editor(
                        df_dados, 
                        use_container_width=True, 
                        height=550, 
                        num_rows="dynamic" if pode_editar else "fixed",
                        disabled=not pode_editar,
                        key=editor_key
                    )
                    
                    col_salvar, col_csv, col_excel = st.columns([1.5, 1, 1])
                    
                    if pode_editar:
                        with col_salvar:
                            if st.button("💾 Salvar Alterações na Nuvem"):
                                if salvar_alteracoes_api(id_planilha, df_editado, aba_selecionada):
                                    registrar_log(st.session_state["usuario_logado"], "Edição Planilha", f"Planilha '{planilha_selecionada}' atualizada")
                                    st.success("Sincronizado com sucesso!")
                                    st.rerun()
                    with col_csv:
                        csv_data = df_editado.to_csv(index=False).encode('utf-8')
                        st.download_button("📥 Exportar CSV", data=csv_data, file_name=f"{planilha_selecionada}.csv", mime="text/csv")
                    
                    with col_excel:
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                            df_editado.to_excel(writer, index=False, sheet_name=aba_selecionada or "Dados")
                        st.download_button("📊 Exportar Excel", data=buffer.getvalue(), file_name=f"{planilha_selecionada}.xlsx", mime="application/vnd.ms-excel")
