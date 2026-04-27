import sys
import os
sys.path.append(os.getcwd())

import google_auth
from googleapiclient.discovery import build
import json

def analyze_values_deep():
    sheet_id = "1a5TNbDmPyLarcak2tLE05HsbLE0t4F_g1az7S0CzlVg"
    
    try:
        creds = google_auth.get_credentials()
        service = build('sheets', 'v4', credentials=creds)
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
        
        deep_analysis = {}
        for sheet_name in sheets:
            # Prendiamo le prime 10 righe per ogni foglio
            range_name = f"'{sheet_name}'!A1:Z10"
            res = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
            rows = res.get("values", [])
            if not rows: continue
            
            headers = rows[0]
            data_rows = rows[1:]
            
            # Troviamo indici di Condizioni e Corredo
            idx_cond = idx_corr = None
            for i, h in enumerate(headers):
                h_low = h.lower()
                if "condizion" in h_low: idx_cond = i
                if "corredo" in h_low or "accessor" in h_low: idx_corr = i
            
            cond_values = set()
            corr_values = set()
            
            for r in data_rows:
                if idx_cond is not None and len(r) > idx_cond: cond_values.add(r[idx_cond].strip())
                if idx_corr is not None and len(r) > idx_corr: corr_values.add(r[idx_corr].strip())
            
            deep_analysis[sheet_name] = {
                "condizioni_headers": [h for h in headers if "condizion" in h.lower()],
                "corredo_headers": [h for h in headers if "corredo" in h.lower() or "accessor" in h.lower()],
                "esempi_condizioni": list(cond_values),
                "esempi_corredo": list(corr_values)
            }
            
        print(json.dumps(deep_analysis, indent=2))
            
    except Exception as e:
        print(f"Errore: {e}")

if __name__ == "__main__":
    analyze_values_deep()
