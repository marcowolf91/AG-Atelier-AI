import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(os.getenv('DATABASE_URL'))
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE products ADD COLUMN size VARCHAR;"))
        conn.commit()
        print("✅ Colonna 'size' aggiunta con successo.")
    except Exception as e:
        if "already exists" in str(e):
            print("ℹ️ La colonna 'size' esiste già.")
        else:
            print(f"❌ Errore: {e}")
