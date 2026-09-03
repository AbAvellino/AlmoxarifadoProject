import os
import sqlite3
import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from PIL import Image

# Criar pasta para salvar imagens do sistema e produtos
if not os.path.exists("uploads"):
    os.makedirs("uploads")

# --- CONEXÃO E INICIALIZAÇÃO DO BANCO DE DADOS ---

def conectar():
    return sqlite3.connect("almoxarifado.db")

def inicializar_banco():
    conn = conectar()
    cursor = conn.cursor()
    
    # Configurações da Empresa
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            id INTEGER PRIMARY KEY DEFAULT 1,
            nome_empresa TEXT DEFAULT 'Sistema de Almoxarifado',
            logo_path TEXT DEFAULT '',
            cor_tema TEXT DEFAULT '#2196F3'
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO configuracoes (id, nome_empresa) VALUES (1, 'Sistema de Almoxarifado')")

    # Produtos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT DEFAULT 'Geral',
            quantidade INTEGER NOT NULL DEFAULT 0,
            foto_path TEXT DEFAULT ''
        )
    """)
    try:
        cursor.execute("ALTER TABLE produtos ADD COLUMN foto_path TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Histórico
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            usuario TEXT DEFAULT 'Sistema',
            data_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (produto_id) REFERENCES produtos (id)
        )
    """)

    # Usuários
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL
        )
    """)

    # Notas Fiscais
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_nf TEXT NOT NULL,
            fornecedor TEXT NOT NULL,
            cnpj_fornecedor TEXT DEFAULT '',
            produto_nome TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            valor_unitario REAL DEFAULT 0.0,
            valor_total REAL DEFAULT 0.0,
            data_recebimento DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT DEFAULT 'Sistema'
        )
    """)

    # Usuários Padrão
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (?, ?, ?)", ("admin", "1234", "Admin"))
        cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (?, ?, ?)", ("operador", "1234", "Operador"))

    conn.commit()
    conn.close()

inicializar_banco()

# --- FUNÇÕES DE CONFIGURAÇÃO E EMPRESA ---

def buscar_configuracoes():
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT nome_empresa, logo_path, cor_tema FROM configuracoes WHERE id = 1")
    res = cursor.fetchone()
    conn.close()
    return {"nome_empresa": res[0], "logo_path": res[1], "cor_tema": res[2]} if res else {"nome_empresa": "Sistema de Almoxarifado", "logo_path": "", "cor_tema": "#2196F3"}

def salvar_configuracoes(nome, logo_path, cor):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("UPDATE configuracoes SET nome_empresa = ?, logo_path = ?, cor_tema = ? WHERE id = 1", (nome, logo_path, cor))
    conn.commit()
    conn.close()

# --- FUNÇÕES DE NEGÓCIO E LÓGICA SQL ---

def autenticar_usuario(usuario, senha):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT perfil FROM usuarios WHERE usuario = ? AND senha = ?", (usuario, senha))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

def buscar_produtos():
    conn = conectar()
    df = pd.read_sql_query("SELECT id, nome, categoria, quantidade, foto_path FROM produtos", conn)
    conn.close()
    return df

def cadastrar_produto(nome, categoria, quantidade, foto_path=""):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO produtos (nome, categoria, quantidade, foto_path) VALUES (?, ?, ?, ?)", (nome, categoria, quantidade, foto_path))
    conn.commit()
    conn.close()

def editar_produto(prod_id, nome, categoria, quantidade, foto_path=""):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("UPDATE produtos SET nome = ?, categoria = ?, quantidade = ?, foto_path = ? WHERE id = ?", (nome, categoria, quantidade, foto_path, prod_id))
    conn.commit()
    conn.close()

def excluir_produto(prod_id):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM produtos WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()

def movimentar_produto(prod_id, tipo, qtd_mov, qtd_atual, usuario_logado):
    if tipo == "SAÍDA" and qtd_mov > qtd_atual:
        return False, f"Estoque insuficiente! Saldo atual: {qtd_atual}"

    nova_qtd = qtd_atual + qtd_mov if tipo == "ENTRADA" else qtd_atual - qtd_mov

    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("UPDATE produtos SET quantidade = ? WHERE id = ?", (nova_qtd, prod_id))
    cursor.execute(
        "INSERT INTO historico (produto_id, tipo, quantidade, usuario) VALUES (?, ?, ?, ?)",
        (prod_id, tipo, qtd_mov, usuario_logado)
    )
    conn.commit()
    conn.close()
    return True, "Movimentação realizada com sucesso!"

def dar_entrada_nota_fiscal(numero_nf, fornecedor, cnpj, nome_prod, qtd_mov, valor_unit, usuario_logado, categoria="Geral"):
    valor_total = qtd_mov * valor_unit

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT id, quantidade FROM produtos WHERE LOWER(nome) = LOWER(?)", (nome_prod.strip(),))
    res_prod = cursor.fetchone()

    if res_prod:
        prod_id, qtd_atual = res_prod
        nova_qtd = qtd_atual + qtd_mov
        cursor.execute("UPDATE produtos SET quantidade = ? WHERE id = ?", (nova_qtd, prod_id))
    else:
        cursor.execute("INSERT INTO produtos (nome, categoria, quantidade) VALUES (?, ?, ?)", (nome_prod.strip(), categoria, qtd_mov))
        prod_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO notas_fiscais (numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, valor_unitario, valor_total, usuario)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (numero_nf, fornecedor, cnpj, nome_prod, qtd_mov, valor_unit, valor_total, usuario_logado))

    cursor.execute(
        "INSERT INTO historico (produto_id, tipo, quantidade, usuario) VALUES (?, ?, ?, ?)",
        (prod_id, f"ENTRADA (NF {numero_nf})", qtd_mov, usuario_logado)
    )

    conn.commit()
    conn.close()
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
            qtd_item = float(get_tag(prod, 'nfe:qCom') if ns else get_tag(prod, 'qCom'))
            val_unit = float(get_tag(prod, 'nfe:vUnCom') if ns else get_tag(prod, 'vUnCom'))

            itens.append({
                "produto": nome_item,
                "quantidade": int(qtd_item),
                "valor_unitario": val_unit,
                "valor_total": int(qtd_item) * val_unit
            })

        return True, {"numero_nf": numero_nf, "fornecedor": fornecedor, "cnpj": cnpj, "itens": itens}
    except Exception as e:
        return False, f"Erro ao ler arquivo XML: {str(e)}"

def buscar_notas_fiscais():
    conn = conectar()
    df = pd.read_sql_query("""
        SELECT id, numero_nf, fornecedor, cnpj_fornecedor, produto_nome, quantidade, 
               valor_unitario, valor_total, data_recebimento, usuario
        FROM notas_fiscais
        ORDER BY id DESC
    """, conn)
    conn.close()
    return df

def buscar_usuarios():
    conn = conectar()
    df = pd.read_sql_query("SELECT id, usuario, senha, perfil FROM usuarios", conn)
    conn.close()
    return df

def cadastrar_usuario(usuario, senha, perfil):
    try:
        conn = conectar()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (?, ?, ?)", (usuario, senha, perfil))
        conn.commit()
        conn.close()
        return True, f"Usuário '{usuario}' cadastrado!"
    except sqlite3.IntegrityError:
        return False, "Usuário já existe!"

def alterar_senha_usuario(usr_id, nova_senha):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("UPDATE usuarios SET senha = ? WHERE id = ?", (nova_senha, usr_id))
    conn.commit()
    conn.close()

def excluir_usuario(usr_id):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id = ?", (usr_id,))
    conn.commit()
    conn.close()

def buscar_historico():
    conn = conectar()
    df = pd.read_sql_query("""
        SELECT h.id, p.nome as produto, h.tipo, h.quantidade, h.usuario, h.data_hora 
        FROM historico h
        LEFT JOIN produtos p ON h.produto_id = p.id
        ORDER BY h.id DESC
    """, conn)
    conn.close()
    return df

# --- CARREGA DADOS DE CONFIGURAÇÃO VISUAL ---
config = buscar_configuracoes()

# --- TELA DE LOGIN & NAVEGAÇÃO ---

if 'logado' not in st.session_state:
    st.session_state.logado = False
    st.session_state.usuario = ""
    st.session_state.perfil = ""

if not st.session_state.logado:
    st.title(f"🔒 {config['nome_empresa']}")
    if config['logo_path'] and os.path.exists(config['logo_path']):
        st.image(config['logo_path'], width=180)
    
    col1, col2 = st.columns([1, 2])
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
        st.rerun()

    # --- ABA 1: ESTOQUE E MOVIMENTAÇÃO ---
    if opcao == "📦 Estoque & Movimentação":
        st.title(f"📦 Controle de Estoque - {config['nome_empresa']}")

        df_prod = buscar_produtos()

        busca = st.text_input("🔍 Buscar produto pelo nome:")
        if busca and not df_prod.empty:
            df_prod = df_prod[df_prod['nome'].str.contains(busca, case=False, na=False)]

        if not df_prod.empty:
            df_prod['Status'] = df_prod['quantidade'].apply(lambda x: "⚠️ REPOR" if x < 5 else "OK")

        st.dataframe(df_prod[['id', 'nome', 'categoria', 'quantidade', 'Status']], use_container_width=True)

        st.subheader("🖼️ Galeria Visual de Produtos")
        if not df_prod.empty:
            cols = st.columns(4)
            for idx, row in df_prod.iterrows():
                col = cols[idx % 4]
                with col:
                    if row['foto_path'] and os.path.exists(row['foto_path']):
                        st.image(row['foto_path'], use_container_width=True)
                    else:
                        st.caption("📷 *Sem Foto*")
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Qtd: {row['quantidade']} | Cat: {row['categoria']}")

        st.write("---")
        st.subheader("🔄 Realizar Movimentação Manual")
        if not df_prod.empty:
            item_selecionado = st.selectbox("Selecione o Produto para Movimentar:", df_prod['nome'].tolist())
            row = df_prod[df_prod['nome'] == item_selecionado].iloc[0]
            
            c1, c2, c3 = st.columns(3)
            with c1:
                tipo_mov = st.selectbox("Tipo:", ["ENTRADA", "SAÍDA"])
            with c2:
                qtd_mov = st.number_input("Quantidade:", min_value=1, step=1)
            with c3:
                st.write(" ")
                st.write(" ")
                if st.button("Confirmar Movimentação", type="primary"):
                    ok, msg = movimentar_produto(
                        int(row['id']), tipo_mov, int(qtd_mov), int(row['quantidade']), st.session_state.usuario
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
        else:
            st.info("Nenhum produto cadastrado.")

        if st.session_state.perfil == "Admin" and not df_prod.empty:
            st.write("---")
            st.subheader("⚙️ Gerenciar Produtos e Fotos (Exclusivo Admin)")
            
            p_sel = st.selectbox("Selecione um Produto para Editar ou Excluir:", df_prod['nome'].tolist(), key="admin_edit_prod")
            row_p = df_prod[df_prod['nome'] == p_sel].iloc[0]

            with st.expander(f"Editar / Excluir: {row_p['nome']}"):
                with st.form("form_edit_prod"):
                    e_nome = st.text_input("Nome do Produto", value=row_p['nome'])
                    e_cat = st.text_input("Categoria", value=row_p['categoria'])
                    e_qtd = st.number_input("Quantidade no Estoque", value=int(row_p['quantidade']), min_value=0)
                    e_foto = st.file_uploader("Atualizar Foto do Produto", type=["jpg", "png", "jpeg"])

                    btn_salvar = st.form_submit_button("💾 Salvar Alterações")
                    
                if btn_salvar:
                    foto_path = row_p['foto_path']
                    if e_foto is not None:
                        foto_path = f"uploads/prod_{row_p['id']}_{e_foto.name}"
                        with open(foto_path, "wb") as f:
                            f.write(e_foto.getbuffer())

                    editar_produto(int(row_p['id']), e_nome, e_cat, int(e_qtd), foto_path)
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
                                item['quantidade'],
                                item['valor_unitario'],
                                st.session_state.usuario
                            )
                            count_sucesso += 1

                        st.success(f"Entrada concluída! {count_sucesso} itens atualizados/cadastrados no estoque.")
                        st.rerun()
                else:
                    st.error(dados_nfe)

        with aba_manual:
            st.subheader("➕ Lançamento Manual de Nota Fiscal")
            df_prod = buscar_produtos()

            with st.form("form_nf_manual"):
                c1, c2 = st.columns(2)
                with c1:
                    num_nf = st.text_input("Número da Nota Fiscal")
                    fornecedor = st.text_input("Fornecedor / Empresa")
                    cnpj = st.text_input("CNPJ (Opcional)")
                    nome_prod = st.text_input("Nome do Produto (Se for novo, será cadastrado)")
                
                with c2:
                    qtd_nf = st.number_input("Quantidade Recebida", min_value=1, step=1)
                    val_unit = st.number_input("Valor Unitário (R$)", min_value=0.0, step=0.01, format="%.2f")
                    st.write(f"**Valor Total Estimado:** R$ {qtd_nf * val_unit:.2f}")

                if st.form_submit_button("Salvar e Dar Entrada"):
                    if num_nf and fornecedor and nome_prod:
                        dar_entrada_nota_fiscal(
                            num_nf, fornecedor, cnpj, nome_prod, int(qtd_nf), val_unit, st.session_state.usuario
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
            st.caption("A planilha deve conter as colunas: **nome**, **categoria**, **quantidade**")

            file_excel = st.file_uploader("Selecione o arquivo .xlsx:", type=["xlsx", "xls"])
            if file_excel is not None:
                try:
                    df_imp = pd.read_excel(file_excel)
                    st.write("### Pré-visualização dos dados:")
                    st.dataframe(df_imp, use_container_width=True)

                    if st.button("🚀 Confirmar e Importar para o Banco de Dados", type="primary"):
                        qtd_importados = 0
                        for idx, row in df_imp.iterrows():
                            cadastrar_produto(
                                str(row['nome']), 
                                str(row.get('categoria', 'Geral')), 
                                int(row.get('quantidade', 0))
                            )
                            qtd_importados += 1
                        st.success(f"{qtd_importados} produtos cadastrados com sucesso!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro ao ler arquivo Excel. Verifique o formato e o nome das colunas: {e}")

        with tab_sheets:
            st.subheader("Sincronização com Google Sheets (Público)")
            st.caption("Cole o ID da planilha do Google Planilhas (Certifique-se de que esteja compartilhada como 'Qualquer pessoa com o link').")

            sheet_id = st.text_input("ID da Planilha do Google Sheets:")
            if sheet_id:
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
                try:
                    df_gsheets = pd.read_csv(url)
                    st.write("### Dados carregados da nuvem:")
                    st.dataframe(df_gsheets, use_container_width=True)

                    if st.button("📥 Importar Dados do Google Sheets", type="primary"):
                        qtd_importados = 0
                        for idx, row in df_gsheets.iterrows():
                            cadastrar_produto(
                                str(row['nome']), 
                                str(row.get('categoria', 'Geral')), 
                                int(row.get('quantidade', 0))
                            )
                            qtd_importados += 1
                        st.success(f"{qtd_importados} produtos importados do Google Sheets!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Não foi possível acessar a planilha. Verifique o ID e as permissões: {e}")

    # --- ABA 4: DASHBOARD ANALYTICS ---
    elif opcao == "📊 Dashboard Analytics":
        st.title("📊 Painel de Indicadores (BI)")

        df_prod = buscar_produtos()

        if not df_prod.empty:
            col1, col2, col3 = st.columns(3)
            col1.metric("Total de Produtos", len(df_prod))
            col2.metric("Itens no Estoque", df_prod['quantidade'].sum())
            col3.metric("Itens Críticos (Reposição)", len(df_prod[df_prod['quantidade'] < 5]))

            st.write("---")

            col_g1, col_g2 = st.columns(2)

            with col_g1:
                st.subheader("Quantidade por Produto")
                fig1, ax1 = plt.subplots()
                ax1.bar(df_prod['nome'], df_prod['quantidade'], color=config['cor_tema'])
                plt.xticks(rotation=45)
                ax1.set_ylabel("Quantidade")
                st.pyplot(fig1)

            with col_g2:
                st.subheader("Distribuição por Categoria")
                cat_df = df_prod.groupby('categoria')['quantidade'].sum()
                fig2, ax2 = plt.subplots()
                ax2.pie(cat_df, labels=cat_df.index, autopct='%1.1f%%', startangle=90)
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
                nome = st.text_input("Nome do Produto")
                cat = st.text_input("Categoria", value="Geral")
                qtd = st.number_input("Quantidade Inicial", min_value=0, step=1)
                foto = st.file_uploader("Foto do Produto (Opcional)", type=["jpg", "png", "jpeg"])

                if st.form_submit_button("Salvar Produto"):
                    if nome:
                        foto_path = ""
                        if foto is not None:
                            foto_path = f"uploads/{foto.name}"
                            with open(foto_path, "wb") as f:
                                f.write(foto.getbuffer())

                        cadastrar_produto(nome, cat, int(qtd), foto_path)
                        st.success("Produto cadastrado com sucesso!")
                        st.rerun()
                    else:
                        st.warning("Preencha o nome do produto!")

    # --- ABA 7: GERENCIAR USUÁRIOS & SENHAS (EXCLUSIVO ADMIN) ---
    elif opcao == "👥 Gerenciar Usuários":
        st.title("👥 Gerenciamento de Usuários e Senhas")
        
        if st.session_state.perfil != "Admin":
            st.error("Acesso Negado: Apenas Administradores podem acessar esta área!")
        else:
            st.subheader("🔑 Usuários Cadastrados & Senhas")
            df_usr = buscar_usuarios()
            st.dataframe(df_usr, use_container_width=True)

            st.write("---")

            col_u1, col_u2 = st.columns(2)

            with col_u1:
                st.subheader("➕ Criar Novo Usuário")
                with st.form("form_usr"):
                    u = st.text_input("Novo Usuário")
                    p = st.text_input("Senha")
                    perfil = st.selectbox("Perfil de Acesso", ["Operador", "Admin"])
                    if st.form_submit_button("Criar Usuário"):
                        if u and p:
                            ok, msg = cadastrar_usuario(u, p, perfil)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                        else:
                            st.warning("Preencha usuário e senha!")

            with col_u2:
                st.subheader("⚙️ Alterar Senha ou Excluir Usuário")
                usr_selecionado = st.selectbox("Selecione o Usuário:", df_usr['usuario'].tolist())
                row_usr = df_usr[df_usr['usuario'] == usr_selecionado].iloc[0]

                nova_senha = st.text_input("Nova Senha", value=str(row_usr['senha']))
                
                c_btn1, c_btn2 = st.columns(2)
                with c_btn1:
                    if st.button("💾 Salvar Nova Senha"):
                        alterar_senha_usuario(int(row_usr['id']), nova_senha)
                        st.success(f"Senha do usuário '{row_usr['usuario']}' alterada!")
                        st.rerun()

                with c_btn2:
                    if row_usr['usuario'] == st.session_state.usuario:
                        st.caption("⚠️ Não é possível excluir o usuário conectado.")
                    else:
                        if st.button("🗑️ Excluir Usuário", type="secondary"):
                            excluir_usuario(int(row_usr['id']))
                            st.warning(f"Usuário '{row_usr['usuario']}' excluído!")
                            st.rerun()

    # --- ABA 8: PERSONALIZAR EMPRESA (EXCLUSIVO ADMIN) ---
    elif opcao == "⚙️ Personalizar Empresa":
        st.title("⚙️ Personalização do Sistema")
        
        if st.session_state.perfil != "Admin":
            st.error("Acesso Negado: Apenas Administradores podem alterar a marca do sistema!")
        else:
            st.subheader("🎨 Personalizar Nome, Logomarca e Cores")
            
            with st.form("form_config"):
                novo_nome = st.text_input("Nome da Empresa / Sistema", value=config['nome_empresa'])
                nova_logo = st.file_uploader("Logomarca da Empresa", type=["png", "jpg", "jpeg"])
                cor_tema = st.color_picker("Cor Principal dos Gráficos", value=config['cor_tema'])

                if st.form_submit_button("💾 Salvar Personalização"):
                    logo_path = config['logo_path']
                    if nova_logo is not None:
                        logo_path = f"uploads/logo_{nova_logo.name}"
                        with open(logo_path, "wb") as f:
                            f.write(nova_logo.getbuffer())

                    salvar_configuracoes(novo_nome, logo_path, cor_tema)
                    st.success("Personalização salva com sucesso!")
                    st.rerun()