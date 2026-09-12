import streamlit as st
import pandas as pd
import psycopg2
from psycopg2 import sql
from datetime import datetime

# ==========================================
# 1. CONFIGURAÇÃO DA PÁGINA E CONEXÃO DB
# ==========================================
st.set_page_config(
    page_title="Sistema de Almoxarifado",
    page_icon="📦",
    layout="wide"
)

def get_connection():
    """Retorna a conexão com o banco PostgreSQL / Supabase através de st.secrets."""
    return psycopg2.connect(
        host=st.secrets["postgres"]["host"],
        database=st.secrets["postgres"]["database"],
        user=st.secrets["postgres"]["user"],
        password=st.secrets["postgres"]["password"],
        port=st.secrets["postgres"]["port"]
    )

# ==========================================
# 2. INICIALIZAÇÃO E AUTOCREATION DE TABELAS
# ==========================================
def init_db():
    """Cria e atualiza automaticamente a tabela 'ferramentas' e garante integridade do esquema."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Cria a tabela de ferramentas se não existir
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ferramentas (
                id SERIAL PRIMARY KEY,
                local VARCHAR(255),
                codigo VARCHAR(100) UNIQUE NOT NULL,
                ferramenta VARCHAR(255) NOT NULL,
                marca VARCHAR(100),
                funcionario VARCHAR(255) DEFAULT 'Almoxarifado',
                ultimo_funcionario VARCHAR(255),
                data_ultimo_emprestimo TIMESTAMP
            );
        """)
        
        # Garante a existência de colunas em caso de migração de tabelas antigas
        colunas_necessarias = [
            ("local", "VARCHAR(255)"),
            ("codigo", "VARCHAR(100)"),
            ("ferramenta", "VARCHAR(255)"),
            ("marca", "VARCHAR(100)"),
            ("funcionario", "VARCHAR(255) DEFAULT 'Almoxarifado'"),
            ("ultimo_funcionario", "VARCHAR(255)"),
            ("data_ultimo_emprestimo", "TIMESTAMP")
        ]
        
        for col, col_type in colunas_necessarias:
            cur.execute(sql.SQL("""
                ALTER TABLE ferramentas ADD COLUMN IF NOT EXISTS {} {};
            """).format(sql.Identifier(col), sql.SQL(col_type)))
            
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        st.error(f"Erro de comunicação/inicialização do Banco de Dados: {e}")

# Executa a checagem das tabelas antes da renderização da interface
init_db()

# ==========================================
# 3. FUNÇÕES DE BUSCA E TRATAMENTO DE ERROS
# ==========================================
@st.cache_data(ttl=5)
def buscar_ferramentas():
    """Busca a lista de ferramentas cadastradas com proteção contra exceções."""
    try:
        conn = get_connection()
        query = """
            SELECT id, local, codigo, ferramenta, marca, funcionario, ultimo_funcionario, data_ultimo_emprestimo 
            FROM ferramentas
            ORDER BY id ASC;
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        init_db()
        colunas = ['id', 'local', 'codigo', 'ferramenta', 'marca', 'funcionario', 'ultimo_funcionario', 'data_ultimo_emprestimo']
        return pd.DataFrame(columns=colunas)

def cadastrar_ferramenta(local, codigo, ferramenta, marca):
    """Insere uma nova ferramenta na base de dados."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ferramentas (local, codigo, ferramenta, marca, funcionario)
            VALUES (%s, %s, %s, %s, 'Almoxarifado')
        """, (local, codigo, ferramenta, marca))
        conn.commit()
        cur.close()
        conn.close()
        st.cache_data.clear()
        return True, "Ferramenta cadastrada com sucesso!"
    except psycopg2.IntegrityError:
        return False, f"O código '{codigo}' já está cadastrado para outra ferramenta."
    except Exception as e:
        return False, f"Erro ao cadastrar ferramenta: {e}"

def registrar_retirada(codigo, novo_funcionario):
    """Registra a retirada transferindo o possuidor atual para 'ultimo_funcionario'."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Busca status atual da ferramenta
        cur.execute("SELECT funcionario FROM ferramentas WHERE codigo = %s", (codigo,))
        res = cur.fetchone()
        
        if not res:
            return False, "Código de ferramenta não encontrado."
        
        possuidor_atual = res[0]
        agora = datetime.now()
        
        # Atualiza substituindo ultimo_funcionario pelo possuidor anterior e registrando o novo
        cur.execute("""
            UPDATE ferramentas
            SET ultimo_funcionario = %s,
                funcionario = %s,
                data_ultimo_emprestimo = %s
            WHERE codigo = %s
        """, (possuidor_atual, novo_funcionario, agora, codigo))
        
        conn.commit()
        cur.close()
        conn.close()
        st.cache_data.clear()
        return True, f"Ferramenta {codigo} retirada com sucesso por {novo_funcionario}!"
    except Exception as e:
        return False, f"Erro ao processar retirada: {e}"

def registrar_devolucao(codigo):
    """Registra a devolução da ferramenta mantendo a rastreabilidade do último responsável."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT funcionario FROM ferramentas WHERE codigo = %s", (codigo,))
        res = cur.fetchone()
        
        if not res:
            return False, "Código de ferramenta não encontrado."
            
        possuidor_atual = res[0]
        
        if possuidor_atual == "Almoxarifado":
            return False, "Esta ferramenta já está no Almoxarifado."
            
        cur.execute("""
            UPDATE ferramentas
            SET ultimo_funcionario = %s,
                funcionario = 'Almoxarifado'
            WHERE codigo = %s
        """, (possuidor_atual, codigo))
        
        conn.commit()
        cur.close()
        conn.close()
        st.cache_data.clear()
        return True, f"Ferramenta {codigo} devolvida com sucesso ao Almoxarifado!"
    except Exception as e:
        return False, f"Erro ao processar devolução: {e}"

# ==========================================
# 4. INTERFACE GRÁFICA STREAMLIT
# ==========================================

st.title("📦 Sistema Integrado de Controle de Almoxarifado")

# Navegação Principal
aba_selecionada = st.sidebar.radio(
    "Selecione o Módulo:",
    ["📋 Checklist de Ferramentas", "🔄 Retirada / Devolução de Materiais"]
)

# ------------------------------------------
# ABA 1: CHECKLIST DE FERRAMENTAS
# ------------------------------------------
if aba_selecionada == "📋 Checklist de Ferramentas":
    st.header("📋 Checklist de Ferramentas e Equipamentos")
    
    sub_aba1, sub_aba2 = st.tabs(["📋 Realizar Checklist", "🛠️ Cadastro / Gestão de Ferramentas"])
    
    with sub_aba1:
        st.subheader("Conferência de Inventário")
        st.caption("Realize a conferência das ferramentas ativas no sistema.")
        
        df_f = buscar_ferramentas()
        if df_f.empty:
            st.info("Nenhuma ferramenta/equipamento cadastrado.")
        else:
            st.dataframe(
                df_f,
                column_config={
                    "id": "ID",
                    "local": "Localização",
                    "codigo": "Código",
                    "ferramenta": "Ferramenta / Equipamento",
                    "marca": "Marca",
                    "funcionario": "Posse Atual",
                    "ultimo_funcionario": "Último Possuidor",
                    "data_ultimo_emprestimo": st.column_config.DatetimeColumn("Última Retirada", format="DD/MM/YYYY HH:mm")
                },
                use_container_width=True,
                hide_index=True
            )

    with sub_aba2:
        st.subheader("🛠️ Cadastrar Nova Ferramenta")
        with st.form("form_cadastro_ferramenta", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                local = st.text_input("Local de Armazenamento (ex: Armário A, Prateleira 2)")
                codigo = st.text_input("Código de Identificação / Patrimônio *")
            with col2:
                ferramenta = st.text_input("Nome da Ferramenta / Equipamento *")
                marca = st.text_input("Marca / Fabricante")
                
            btn_cadastrar = st.form_submit_button("Salvar Cadastramento")
            
            if btn_cadastrar:
                if not codigo or not ferramenta:
                    st.warning("Preencha os campos obrigatórios (*) para continuar.")
                else:
                    sucesso, msg = cadastrar_ferramenta(local, codigo, ferramenta, marca)
                    if sucesso:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                        
        st.markdown("---")
        st.subheader("Ferramentas Cadastradas")
        df_gestao = buscar_ferramentas()
        st.dataframe(df_gestao, use_container_width=True, hide_index=True)


# ------------------------------------------
# ABA 2: RETIRADA / DEVOLUÇÃO DE MATERIAIS
# ------------------------------------------
elif aba_selecionada == "🔄 Retirada / Devolução de Materiais":
    st.header("🔄 Retirada / Devolução de Materiais e Ferramentas")
    
    tipo_operacao = st.radio("Selecione a Operação:", ["🔧 Retirada de Ferramentas", "↩️ Devolução de Ferramentas"], horizontal=True)
    
    df_f = buscar_ferramentas()
    
    if tipo_operacao == "🔧 Retirada de Ferramentas":
        st.subheader("Registrar Saída de Ferramenta")
        
        if df_f.empty:
            st.warning("Nenhuma ferramenta disponível para retirada no momento.")
        else:
            # Filtra apenas ferramentas no almoxarifado para facilitar a seleção
            ferramentas_disponiveis = df_f[df_f['funcionario'] == 'Almoxarifado']
            
            with st.form("form_retirada"):
                if not ferramentas_disponiveis.empty:
                    opcoes = [f"{row['codigo']} - {row['ferramenta']}" for _, row in ferramentas_disponiveis.iterrows()]
                    selecionado = st.selectbox("Selecione a Ferramenta Disponível:", opcoes)
                    cod_retirada = selecionado.split(" - ")[0]
                else:
                    cod_retirada = st.text_input("Código da Ferramenta:")
                    st.info("Todas as ferramentas cadastradas estão atualmente em uso/emprestadas.")
                    
                nome_funcionario = st.text_input("Nome do Colaborador que está Retirando *")
                btn_retirar = st.form_submit_button("Confirmar Retirada")
                
                if btn_retirar:
                    if not cod_retirada or not nome_funcionario:
                        st.warning("Informe a ferramenta e o nome do colaborador.")
                    else:
                        sucesso, msg = registrar_retirada(cod_retirada, nome_funcionario)
                        if sucesso:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

    elif tipo_operacao == "↩️ Devolução de Ferramentas":
        st.subheader("Registrar Devolução ao Almoxarifado")
        
        ferramentas_emprestadas = df_f[df_f['funcionario'] != 'Almoxarifado']
        
        if ferramentas_emprestadas.empty:
            st.info("Todas as ferramentas estão atualmente no Almoxarifado.")
        else:
            with st.form("form_devolucao"):
                opcoes_dev = [f"{row['codigo']} - {row['ferramenta']} (Com: {row['funcionario']})" for _, row in ferramentas_emprestadas.iterrows()]
                selecionado_dev = st.selectbox("Selecione a Ferramenta a ser Devolvida:", opcoes_dev)
                cod_devolucao = selecionado_dev.split(" - ")[0]
                
                btn_devolver = st.form_submit_button("Confirmar Devolução")
                
                if btn_devolver:
                    sucesso, msg = registrar_devolucao(cod_devolucao)
                    if sucesso:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
