from database import SessionLocal, Product
import json

def clear_wrong_associations():
    db = SessionLocal()
    try:
        # Pulizia mirata: tutto ciò che è stato aggiornato nel batch "incriminato"
        # ESCLUDENDO la categoria 'Borse Donna' che l'utente ha confermato essere corretta.
        
        target_timestamp = "2026-05-14 16:35:40"
        
        products = db.query(Product).filter(
            Product.category != 'Borse Donna',
            Product.updated_at.like(f"{target_timestamp}%")
        ).all()
        
        count = 0
        for p in products:
            p.matched_images_json = "[]"
            p.drive_folder_id = None
            p.drive_folder_url = None
            p.image_match_score = 0.0
            count += 1
            
        db.commit()
        print(f"🧹 Pulizia completata: {count} prodotti resettati (Borse Donna salvaguardate).")
    except Exception as e:
        print(f"❌ Errore durante la pulizia: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    clear_wrong_associations()
