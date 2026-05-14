import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Configurazione
TOKEN_FILE = '/Users/marcosimonelli/Desktop/AG Atelier AI/token.json'

def check_drive_names():
    if not os.path.exists(TOKEN_FILE): return

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        if creds.expired and creds.refresh_token: creds.refresh(Request())
        service = build('drive', 'v3', credentials=creds)

        # Cartella BORSE
        folder_id = "1Sw_xPnu5C3D40bCgCmDDwjik0qnyA9j5"
        print(f"🔍 Scansionando cartella BORSE: {folder_id}")

        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id, name, mimeType)",
            pageSize=50
        ).execute()
        files = results.get('files', [])

        for f in files:
            print(f"- {f['name']} ({f['mimeType']})")
            if f['mimeType'] == 'application/vnd.google-apps.folder':
                # Scansioniamo un livello in più
                print(f"   📂 Entrando in: {f['name']}")
                sub = service.files().list(
                    q=f"'{f['id']}' in parents and trashed = false",
                    fields="files(id, name, mimeType)",
                    pageSize=10
                ).execute()
                for sf in sub.get('files', []):
                    print(f"      📄 {sf['name']} ({sf['mimeType']})")
                
    except Exception as e:
        print(f"❌ Errore API: {e}")

if __name__ == "__main__":
    check_drive_names()
