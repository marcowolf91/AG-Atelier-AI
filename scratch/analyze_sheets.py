import sys
import os
sys.path.append(os.getcwd())

import google_auth
from googleapiclient.discovery import build
import json

def analyze_all_sheets():
    sheet_id = "1a5TNbDmPyLarcak2tLE05HsbLE0t4F_g1az7S0CzlVg"
    
    try:
        creds = google_auth.get_credentials()
        service = build('sheets', 'v4', credentials=creds)
        
        # Otteniamo tutti i nomi dei fogli
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
        
        results = {}
        for sheet_name in sheets:
            range_name = f"'{sheet_name}'!A1:Z1"
            res = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
            headers = res.get("values", [[]])[0]
            results[sheet_name] = headers
            
        print(json.dumps(results, indent=2))
            
    except Exception as e:
        print(f"Errore: {e}")

if __name__ == "__main__":
    analyze_all_sheets()
