import os, json, sys
# Aggiungi la root del progetto al path
sys.path.append(os.getcwd())

import google_auth
from googleapiclient.discovery import build

def test():
    print("🔍 Avvio Test Connettività Drive...")
    creds = google_auth.get_credentials()
    if not creds:
        print("❌ ERRORE: Credenziali non trovate o non valide!")
        return

    try:
        service = build('drive', 'v3', credentials=creds)
        
        # Leggiamo config
        conf_file = "workspace_config.json"
        if not os.path.exists(conf_file):
            print(f"❌ ERRORE: {conf_file} non esiste!")
            return
            
        with open(conf_file, 'r') as f:
            conf = json.load(f)
            fid = conf.get("folder_id")
            
        print(f"📂 Tentativo accesso cartella: {fid}")
        res = service.files().get(fileId=fid, fields="id, name", supportsAllDrives=True).execute()
        print(f"✅ SUCCESSO! Cartella trovata: {res.get('name')} ({res.get('id')})")
        
        print("🔭 Scansione file (primi 5)...")
        q = f"'{fid}' in parents and trashed = false"
        files_res = service.files().list(q=q, pageSize=5, fields="files(id, name)", supportsAllDrives=True).execute()
        files = files_res.get('files', [])
        if not files:
            print("⚠️ Nessun file trovato nella cartella sorgente.")
        for f in files:
            print(f" - [{f['id']}] {f['name']}")
            
    except Exception as e:
        print(f"❌ ERRORE DRIVE API: {str(e)}")

if __name__ == "__main__":
    test()
