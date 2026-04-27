import json, os, sys
sys.path.append(os.getcwd())
try:
    import google_auth
    creds = google_auth.get_credentials()
    
    with open("workspace_config.json", 'r') as f:
        cnf = json.load(f)
    out_id = cnf.get("folder_out_id")
    
    from googleapiclient.discovery import build
    drive_service = build('drive', 'v3', credentials=creds)
    res = drive_service.files().list(q=f"'{out_id}' in parents and trashed=false", fields="files(id, name, mimeType)", pageSize=20).execute()
    files = res.get('files', [])
    print("Files in Complete root:", files)

    for f in [x for x in files if x.get('mimeType') == 'application/vnd.google-apps.folder']:
        print("Checking subfolder:", f['name'])
        res2 = drive_service.files().list(q=f"'{f['id']}' in parents and trashed=false", fields="files(id, name)", pageSize=5).execute()
        f2 = res2.get('files', [])
        print("  Contents:", f2)
except Exception as e:
    print("FATAL:", e)
