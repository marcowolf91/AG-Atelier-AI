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
        "shopify_url": "SHOPIFY_STORE_URL",
        "shopify_client_id": "SHOPIFY_CLIENT_ID",
        "shopify_client_secret": "SHOPIFY_CLIENT_SECRET"
    }
    
    var_name = env_vars_map.get(service)
    if var_name:
        set_key(ENV_FILE, var_name, key)
        return True
    return False

def get_api_key(service: str):
    """Legge una chiave (mascherata per la UI) dall'ambiente in modo human-friendly."""
    val = get_raw_api_key(service)
    if val:
        val = val.strip()
        if len(val) > 10:
            return f"{val[:6]}...{val[-4:]}"
        return "*******"
    return None

def get_raw_api_key(service: str):
    """Legge la chiave reale (non mascherata) dall'ambiente per l'uso interno."""
    load_dotenv(ENV_FILE, override=True)
    env_vars_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "serper": "SERPER_API_KEY",
        "shopify_token": "SHOPIFY_ADMIN_TOKEN",
        "shopify_url": "SHOPIFY_STORE_URL",
        "shopify_client_id": "SHOPIFY_CLIENT_ID",
        "shopify_client_secret": "SHOPIFY_CLIENT_SECRET"
    }
    var_name = env_vars_map.get(service)
    if var_name:
        return os.getenv(var_name)
    return None

def check_google_auth():
    import google_auth
    return google_auth.is_google_connected()

# NOTA: Per il vero flusso OAuth Google serve un file client_secrets.json (creato su Google Cloud Console)
# Implementeremo qui uno stub che l'app chiamerà.
def start_google_oauth_flow():
    # Se ci fosse il file credentials.json, qua inizializzeremmo l'InstalledAppFlow
    pass
