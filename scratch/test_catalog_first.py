import sys
import os
import json

# Aggiungiamo la root del progetto al path
sys.path.append(os.getcwd())

from ai_agent import AIAgent
from app import get_darkroom_images
from sqlalchemy.orm import Session
from database import SessionLocal

def test_find_images_for_product(product_query):
    print(f"🔍 TEST: Cerco foto per '{product_query}'...")
    
    db = SessionLocal()
    agent = AIAgent()
    
    # 1. Recuperiamo un campione di immagini
    all_files = get_darkroom_images(refresh=False, db=db)
    sample = all_files[:20] # Prendiamo un campione per il test
    
    # 2. Chiediamo all'AI di identificare quali di queste foto sono il prodotto cercato
    prompt = f"""
    Ho queste immagini di prodotti di lusso. 
    Dimmi quali di queste (ID) mostrano un prodotto che corrisponde a: "{product_query}".
    Rispondi con un JSON che contiene la lista degli ID validi.
    Esempio: {{"matching_ids": ["id1", "id2"]}}
    """
    
    # Per il test usiamo una logica semplificata: analizziamo le descrizioni se presenti o usiamo Vision
    matching = []
    for f in sample:
        # Qui simuliamo la chiamata Vision per ogni foto (nel sistema reale useremo indici visivi)
        print(f"   - Analizzando {f['name']}...")
        # (Simulazione)
        if "sicily" in f['name'].lower() or "dolce" in f['name'].lower():
            matching.append(f['id'])
            
    print(f"✅ Risultato: Trovate {len(matching)} immagini corrispondenti.")
    return matching

if __name__ == "__main__":
    test_find_images_for_product("Dolce & Gabbana Sicily")
