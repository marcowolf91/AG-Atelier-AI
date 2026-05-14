from database import SessionLocal, Product, ProductStatus
import json

def reset_failed_publications():
    db = SessionLocal()
    try:
        # Lista di SKU identificati dai log come pubblicati con 0 foto oggi
        # Usiamo una query per trovare i prodotti Published che hanno immagini nel DB locale
        # ma che probabilmente sono andati online senza foto (secondo i log del bridge).
        
        # Per sicurezza, resettiamo tutti i prodotti 'Published' che hanno almeno un'immagine nel DB locale
        # ma che l'utente dice essere "vuoti" su Shopify.
        
        products = db.query(Product).filter(
            Product.status == ProductStatus.Published,
            Product.matched_images_json != None,
            Product.matched_images_json != '',
            Product.matched_images_json != '[]'
        ).all()
        
        count = 0
        for p in products:
            p.status = ProductStatus.Ready
            count += 1
            
        db.commit()
        print(f"✅ Reset completato: {count} prodotti riportati in stato 'Ready'.")
    except Exception as e:
        print(f"❌ Errore durante il reset: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    reset_failed_publications()
