# 📦 Sistema de Gestão de Almoxarifado

Um sistema completo de controle de estoque, gestão de entradas por Nota Fiscal (NF-e) e análise de dados (BI), desenvolvido em Python utilizando **Streamlit**, **Pandas** e **SQLite**.

---

## 🚀 Funcionalidades

- **📦 Controle de Estoque:** Cadastro, busca rápida, movimentações manuais (entrada/saída) e alerta visual de reposição de produtos.
- **🖼️ Galeria Visual:** Visualização de imagens dos produtos cadastrados.
- **🧾 Leitura Automática de NF-e (XML):** Importação de arquivos XML de Nota Fiscal Eletrônica com lançamento automático de novos itens no estoque e registro de histórico.
- **📥 Importação em Lote:** Suporte a carga massiva de produtos via planilhas **Excel (.xlsx)** e integração direta com **Google Sheets**.
- **📊 Dashboard Analytics:** Painel com indicadores gráficos (Matplotlib/Pandas) para visualização de volume por produto e categorias.
- **🔑 Gestão de Usuários & Controle de Acesso:** Perfis diferenciados (*Admin* e *Operador*) com alteração de senha e restrições de gerenciamento.
- **⚙️ Personalização de Marca:** Configuração do nome da empresa, logomarca e paleta de cores dos gráficos em tempo real.
- **📋 Auditoria / Histórico:** Registro detalhado de todas as operações realizadas no sistema.

---

## 🛠️ Tecnologias Utilizadas

- **Linguagem:** Python 3
- **Interface Web:** [Streamlit](https://streamlit.io/)
- **Manipulação de Dados:** Pandas
- **Gráficos & BI:** Matplotlib
- **Banco de Dados:** SQLite3
- **Processamento de XML:** ElementTree
- **Manipulação de Imagens:** Pillow (PIL)

---

## 📂 Estrutura do Projeto

```text
├── app.py              # Aplicação principal Streamlit
├── requirements.txt    # Dependências do projeto
├── almoxarifado.db     # Banco de dados SQLite (gerado automaticamente)
└── uploads/            # Pasta reservada para fotos e logos (gerada automaticamente)
