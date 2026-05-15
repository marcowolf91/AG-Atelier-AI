from database import SessionLocal, Product, ProductStatus
import json

def selective_reset():
    db = SessionLocal()
    try:
        # Troviamo tutti i prodotti che NON sono "Borse Donna"
        # e che hanno associazioni di immagini (o sono in stati avanzati dovuti a errori di matching)
        target_sheet = "Borse Donna"
        
        products = db.query(Product).filter(Product.source_sheet != target_sheet).all()
        
        count = 0
        for p in products:
            # Resettiamo solo se c'è qualcosa da resettare o se lo stato è incoerente
            if p.matched_images_json != "[]" or p.drive_folder_id:
                p.matched_images_json = "[]"
                p.drive_folder_id = None
                p.drive_folder_url = None
                p.image_match_score = 0.0
                
                # Se era stato erroneamente marcato come MATCHED o oltre, lo riportiamo in Draft
                if p.status in [ProductStatus.MATCHED, ProductStatus.Validating, ProductStatus.Ready]:
                     p.status = ProductStatus.Draft
                
                count += 1
        
        db.commit()
        print(f"✅ Reset completato per {count} prodotti.")
        print(f"ℹ️ I prodotti della categoria '{target_sheet}' sono stati preservati.")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Errore durante il reset: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    selective_reset()
