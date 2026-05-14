import json
import os
import time
from database import SessionLocal, Product, ProductStatus
from sqlalchemy import text

def run_recovery():
    db = SessionLocal()
    try:
        # 1. Carichiamo la cache della Darkroom
        if not os.path.exists("darkroom_cache.json"):
            print("❌ darkroom_cache.json non trovato. Esegui prima una scansione dalla UI.")
            return
            
        with open("darkroom_cache.json", "r") as f:
            cache = json.load(f)
            all_files = cache.get("raw_files", [])
            
        print(f"🔍 Trovati {len(all_files)} asset in cache.")
        
        # Mappa row -> [file_ids]
        import re
        row_to_files = {}
        for f in all_files:
            match = re.match(r'^(\d+)', f['name'])
            if match:
                row_num = int(match.group(1))
                if row_num not in row_to_files: row_to_files[row_num] = []
                row_to_files[row_num].append(f['id'])
        
        print(f"📦 Mappate {len(row_to_files)} righe con immagini.")
        
        # 2. Cerchiamo prodotti da ripristinare
        # Priorità a quelli pubblicati senza immagini o quelli in Ready senza immagini
        products = db.query(Product).all()
        
        count = 0
        for p in products:
            if not p.original_sheets_row: continue
            
            # Se ha già immagini, saltiamo (a meno che non vogliamo forzare)
            if p.matched_images_json and p.matched_images_json != "[]" and p.matched_images_json != 'null':
                continue
                
            fids = row_to_files.get(p.original_sheets_row)
            if fids:
                print(f"✨ Ripristino ID {p.id} ({p.brand} {p.model}) -> Riga {p.original_sheets_row} -> {len(fids)} immagini")
                p.matched_images_json = json.dumps(fids)
                
                # Se era Published, rimane Published ma ora ha le foto (saranno caricate al prossimo sync o manualmente)
                # Se era Ready, lo portiamo in Validating (come se fosse stato matchato ora)
                if p.status == ProductStatus.Ready:
                    p.status = ProductStatus.Validating
                
                count += 1
        
        db.commit()
        print(f"✅ Recovery completato. {count} prodotti aggiornati.")
        
    finally:
        db.close()

if __name__ == "__main__":
    run_recovery()
