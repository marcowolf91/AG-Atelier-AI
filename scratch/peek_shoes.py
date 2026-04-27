import sys
import os
sys.path.append(os.getcwd())

import google_auth
from googleapiclient.discovery import build
import json

def peek_sheet():
    sheet_id = "1a5TNbDmPyLarcak2tLE05HsbLE0t4F_g1az7S0CzlVg"
    range_name = "'Scarpe Uomo'!A1:F5"
    
    try:
        creds = google_auth.get_credentials()
        service = build('sheets', 'v4', credentials=creds)
        res = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
        rows = res.get("values", [])
        
        print(f"--- DATI FOGLIO 'Scarpe Uomo' ---")
        for i, row in enumerate(rows):
            print(f"Riga {i+1}: {row}")
            
    except Exception as e:
        print(f"Errore: {e}")

if __name__ == "__main__":
    peek_sheet()
