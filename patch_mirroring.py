
import os

with open('app.py', 'r') as f:
    content = f.read()

old_block = """        # Logica di destinazione (Mirror o Root Out)
        source_parent_id = meta.get('parents', [None])[0]
        target_folder_id = target_root_id or source_parent_id

        if target_root_id and source_parent_id:
            # Specchiamento sottocartella
            p_meta = drive_service.files().get(fileId=source_parent_id, fields='name', supportsAllDrives=True).execute()
            f_name = p_meta.get('name')
            q = f"name = '{f_name}' and '{target_root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res = drive_service.files().list(q=q, fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            folders = res.get('files', [])
            if folders: target_folder_id = folders[0]['id']
            else:
                f_body = {'name': f_name, 'parents': [target_root_id], 'mimeType': 'application/vnd.google-apps.folder'}
                new_f = drive_service.files().create(body=f_body, fields='id', supportsAllDrives=True).execute()
                target_folder_id = new_f['id']"""

new_block = """        # Logica di destinazione (Deep Mirroring)
        source_parent_id = meta.get('parents', [None])[0]
        target_folder_id = target_root_id or source_parent_id
        
        # Carico la radice sorgente per sapere dove fermarmi con il mirroring
        source_root_id = cnf.get("folder_id")

        if target_root_id and source_parent_id and source_root_id:
            try:
                # Risalgo la gerarchia dal file fino alla radice sorgente per costruire il percorso
                path_folders = []
                curr_id = source_parent_id
                while curr_id and curr_id != source_root_id:
                    f_meta = drive_service.files().get(fileId=curr_id, fields='id, name, parents', supportsAllDrives=True).execute()
                    path_folders.insert(0, f_meta.get('name'))
                    parents = f_meta.get('parents', [])
                    curr_id = parents[0] if parents else None
                
                # Ora replico il percorso nella destinazione
                last_target_parent = target_root_id
                for folder_name in path_folders:
                    q = f"name = '{folder_name}' and '{last_target_parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                    res = drive_service.files().list(q=q, fields='files(id)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
                    existing = res.get('files', [])
                    if existing:
                        last_target_parent = existing[0]['id']
                    else:
                        f_body = {'name': folder_name, 'parents': [last_target_parent], 'mimeType': 'application/vnd.google-apps.folder'}
                        new_f = drive_service.files().create(body=f_body, fields='id', supportsAllDrives=True).execute()
                        last_target_parent = new_f['id']
                
                target_folder_id = last_target_parent
            except Exception as e:
                print(f"⚠️ Mirroring Path Error: {e}")
                target_folder_id = target_root_id"""

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('app.py', 'w') as f:
        f.write(content)
    print("SUCCESS: Deep Mirroring logic applied.")
else:
    print("ERROR: Could not find the old block to replace.")
