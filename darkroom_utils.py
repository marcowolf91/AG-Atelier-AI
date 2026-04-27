import os
import json
import time
import google_auth

_DRIVE_SERVICE = None

def get_drive_service():
    """Restituisce il servizio Drive riutilizzando la connessione (Singleton)."""
    global _DRIVE_SERVICE
    if _DRIVE_SERVICE:
        return _DRIVE_SERVICE
    
    creds = google_auth.get_credentials()
    if not creds:
        return None
        
    from googleapiclient.discovery import build
    _DRIVE_SERVICE = build('drive', 'v3', credentials=creds)
    return _DRIVE_SERVICE

def clear_drive_cache():
    """Rimuove il file di cache per forzare una scansione fresca."""
    if os.path.exists("darkroom_cache.json"):
        os.remove("darkroom_cache.json")
        return True
    return False
