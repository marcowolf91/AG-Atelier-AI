import json, os
from googleapiclient.discovery import build
import google_auth

creds = google_auth.get_credentials()
if not creds:
    print("NO CREDS")
    exit(1)

drive_service = build('drive', 'v3', credentials=creds)

with open("workspace_config.json", 'r') as f:
    cnf = json.load(f)

target_id = cnf.get("folder_out_id")
print("Target Root:", target_id)

try:
    from pillow_heif import register_heif_opener
    import io
    from PIL import Image
    register_heif_opener()
    print("pillow-heif loaded successfully")
except Exception as e:
    print("Error loading pillow-heif:", e)

