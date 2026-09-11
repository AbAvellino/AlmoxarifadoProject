import google.generativeai as genai
import os

# Configuração da chave de API (certifique-se de que a variável de ambiente está definida ou passe a chave diretamente)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", "SUA_CHAVE_AQUI"))

# Inicialização do modelo atualizado para gemini-3.6-flash
model = genai.GenerativeModel("gemini-3.6-flash")

def iniciar_assistente():
    """Inicializa uma sessão de chat com a Fox Assistente."""
    chat = model.start_chat(history=[])
    return chat

def enviar_mensagem(chat, mensagem_usuario: str) -> str:
    """Envia uma mensagem para o chat e retorna a resposta da Fox Assistente."""
    try:
        response = chat.send_message(mensagem_usuario)
        return response.text
    except Exception as e:
        return f"Erro ao conversar com a Fox Assistente: {e}"

# Exemplo de execução
if __name__ == "__main__":
    assistente = iniciar_assistente()
    
    # Exemplo de interação
    mensagem = "oie"
    resposta = enviar_mensagem(assistente, mensagem)
    print(f"Usuário: {mensagem}")
    print(f"Fox Assistente: {resposta}")
