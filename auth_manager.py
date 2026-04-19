import os
from dotenv import set_key, load_dotenv

# Path per il file .env locale
ENV_FILE = ".env"

# Carica l'ambiente all'avvio
load_dotenv(ENV_FILE)

def save_api_key(service: str, key: str):
    """Salva o aggiorna una chiave API in modo sicuro nel .env localmente."""
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w") as f:
            f.write("") # Crea file vuoto se non esiste
            
    # Mappa i nomi servizio a nomi variabili env previsti
    env_vars_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "serper": "SERPER_API_KEY",
        "shopify_token": "SHOPIFY_ADMIN_TOKEN",
        "shopify_url": "SHOPIFY_STORE_URL"
    }
    
    var_name = env_vars_map.get(service)
    if var_name:
        set_key(ENV_FILE, var_name, key)
        return True
    return False

def get_api_key(service: str):
    """Legge una chiave (parzialmente coperta) dall'ambiente."""
    load_dotenv(ENV_FILE, override=True)
    env_vars_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "serper": "SERPER_API_KEY",
        "shopify_token": "SHOPIFY_ADMIN_TOKEN",
        "shopify_url": "SHOPIFY_STORE_URL"
    }
    
    var_name = env_vars_map.get(service)
    if var_name:
        val = os.getenv(var_name)
        if val:
            # Ritorna mascherata per non sbirciare in UI tutto il JWT
            if len(val) > 8:
                return f"{val[:4]}...{val[-4:]}"
            return "***"
    return None

def check_google_auth():
    import google_auth
    return google_auth.is_google_connected()

# NOTA: Per il vero flusso OAuth Google serve un file client_secrets.json (creato su Google Cloud Console)
# Implementeremo qui uno stub che l'app chiamerà.
def start_google_oauth_flow():
    # Se ci fosse il file credentials.json, qua inizializzeremmo l'InstalledAppFlow
    pass
