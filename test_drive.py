import os
import sys
# Set token path properly
os.chdir('/Users/marcosimonelli/Desktop/AG Atelier AI')
from google_auth import get_credentials
from googleapiclient.discovery import build

creds = get_credentials()
if not creds:
    print("NO CREDS")
    sys.exit()
service = build('drive', 'v3', credentials=creds)

mime_query = "(mimeType='application/vnd.google-apps.folder' or mimeType='application/vnd.google-apps.spreadsheet')"
try:
    results = service.files().list(
        q=f"'root' in parents and {mime_query} and trashed=false",
        fields="files(id, name, mimeType)",
        pageSize=50,
        orderBy="folder, name"
    ).execute()
    print("Files:", len(results.get('files', [])))
    for x in results.get('files', [])[:5]:
        print(x['name'], x['mimeType'])
except Exception as e:
    print("ERROR:", e)
