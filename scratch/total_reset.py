from database import SessionLocal, Product, ProductStatus

def total_reset():
    db = SessionLocal()
    try:
        products = db.query(Product).all()
        
        count = 0
        for p in products:
            p.matched_images_json = "[]"
            p.drive_folder_id = None
            p.drive_folder_url = None
            p.image_match_score = 0.0
            
            # Riportiamo tutto a Draft per permettere allo SmartMapper di lavorare su una coda pulita
            p.status = ProductStatus.Draft
            count += 1
        
        db.commit()
        print(f"✅ RESET TOTALE COMPLETATO: {count} prodotti resettati.")
        print("🚀 Ora puoi lanciare 'Sincronizza Foto' dalla Sala Macchine.")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Errore durante il reset totale: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    total_reset()
