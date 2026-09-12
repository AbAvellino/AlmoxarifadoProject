import streamlit as st
import pandas as pd
import psycopg2
from psycopg2 import sql

# --- CONEXÃO COM O BANCO DE DADOS (PostgreSQL / Supabase) ---
def get_connection():
    # Utiliza as credenciais salvas em st.secrets [postgres] ou [db]
    return psycopg2.connect(
        host=st.secrets["postgres"]["host"],
        database=st.secrets["postgres"]["database"],
        user=st.secrets["postgres"]["user"],
        password=st.secrets["postgres"]["password"],
        port=st.secrets["postgres"]["port"]
    )

# --- INICIALIZAÇÃO E CRIAÇÃO DAS TABELAS ---
def init_db():
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Criação da tabela de ferramentas caso não exista
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ferramentas (
                id SERIAL PRIMARY KEY,
                local VARCHAR(255),
                codigo VARCHAR(100) UNIQUE,
                ferramenta VARCHAR(255) NOT NULL,
                marca VARCHAR(100),
                funcionario VARCHAR(255),
                ultimo_funcionario VARCHAR(255),
                data_ultimo_emprestimo TIMESTAMP
            );
        """)
        
        # Se você tiver outras tabelas (ex: historico, movimentacoes, etc), adicione aqui os CREATE TABLE IF NOT EXISTS
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        st.error(f"Erro ao inicializar o banco de dados: {e}")

# Executa a inicialização do banco ao carregar o aplicativo
init_db()


# --- FUNÇÃO DE BUSCA COM TRATAMENTO DE ERRO ---
@st.cache_data(ttl=60)
def buscar_ferramentas():
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
        # Se a tabela não existir por algum motivo, tenta criar e retorna DataFrame vazio
        init_db()
        # Retorna estrutura de DataFrame vazia com as colunas esperadas para não quebrar a interface
        colunas = ['id', 'local', 'codigo', 'ferramenta', 'marca', 'funcionario', 'ultimo_funcionario', 'data_ultimo_emprestimo']
        return pd.DataFrame(columns=colunas)


# --- EXEMPLE DE UTILIZAÇÃO NO APP ---
st.title("📋 Checklist de Ferramentas e Equipamentos")

# Busca os dados com segurança
df_f = buscar_ferramentas()

if df_f.empty:
    st.info("Nenhuma ferramenta/equipamento cadastrado.")
else:
    st.dataframe(df_f)
