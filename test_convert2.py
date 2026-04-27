import json, os, sys
sys.path.append(os.getcwd())
try:
    from app import process_conversions
    import google_auth
    creds = google_auth.get_credentials()
    
    with open("workspace_config.json", 'r') as f:
        cnf = json.load(f)
    root_id = cnf.get("folder_id")
    
    from googleapiclient.discovery import build
    drive_service = build('drive', 'v3', credentials=creds)
    # find 1 heic
    res = drive_service.files().list(q=f"'{root_id}' in parents and name contains '.heic'", fields="files(id, name)", pageSize=1).execute()
    files = res.get('files', [])
    if files:
        print("Testing with file:", files[0]['name'])
        process_conversions([files[0]['id']], creds)
    else:
        # maybe in subfolders?
        res = drive_service.files().list(q=f"'{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder'", fields="files(id)", pageSize=1).execute()
        sf = res.get('files', [])
        if sf:
            sub = sf[0]['id']
            res2 = drive_service.files().list(q=f"'{sub}' in parents and name contains '.heic'", fields="files(id, name)", pageSize=1).execute()
            f2 = res2.get('files', [])
            if f2:
                print("Testing with file in subfolder:", f2[0]['name'])
                process_conversions([f2[0]['id']], creds)
            else:
                print("No HEIC found for test")
except Exception as e:
    print("FATAL:", e)
