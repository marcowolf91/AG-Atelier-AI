import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

print("🔍 [DEBUG] Controllo Database Supabase...")
try:
    # Conteggio totale
    total = db.execute(text("SELECT count(*) FROM products")).scalar()
    print(f"📊 Totale prodotti: {total}")
    
    # Conteggio per stato
    stats = db.execute(text("SELECT status, count(*) FROM products GROUP BY status")).fetchall()
    print("\n📈 Prodotti per Stato:")
    for s, count in stats:
        print(f"   - {s}: {count}")
        
    # Controllo immagini
    with_images = db.execute(text("SELECT id, status, matched_images_json FROM products WHERE matched_images_json IS NOT NULL AND matched_images_json != '[]' AND matched_images_json != '' LIMIT 10")).fetchall()
    print(f"\n🖼️ Prodotti con immagini associate (limit 10): {len(with_images)}")
    for pid, status, images in with_images:
        print(f"   ID: {pid} | Stato: {status} | Immagini: {images[:50]}...")

except Exception as e:
    print(f"❌ Errore: {e}")
finally:
    db.close()
