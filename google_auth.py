import os
import json
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Autorizzazioni necessarie per Drive e Sheets
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"

def get_google_auth_url(redirect_uri="http://localhost:8002/auth/google/callback"):
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return None, "File client_secrets.json non trovato nella cartella principale."
        
    # Per permettere oauthlib di usare HTTP locale senza crash
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    return authorization_url, state

def handle_callback(code, url="http://localhost:8002/auth/google/callback", full_url=None, state=None):
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=url
    )
    
    # fetch_token usando l'intero URL di callback
    flow.fetch_token(authorization_response=full_url)
    credentials = flow.credentials
    
    with open(TOKEN_FILE, 'w') as token:
        token.write(credentials.to_json())
        
    return True

def is_google_connected():
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
            return creds.valid
        except Exception:
            return False
    return False
