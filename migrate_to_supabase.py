import os
import sqlite3
import json
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from database import Base, Product, CategoryGovernance, CategoryRule, HarvesterSetting, GlobalTagGovernance, Setting

load_dotenv()

# Configurazione
SQLITE_DB = "atelier_ai.db"
# La DATABASE_URL deve essere nel formato: postgresql://postgres:[PASSWORD]@db.uvhiwrrobqeteeidkatm.supabase.co:5432/postgres
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL or "METTI_QUI_LA_TUA_PASSWORD" in DATABASE_URL:
    print("❌ ERRORE: Devi impostare la DATABASE_URL con la tua password nel file .env")
    exit(1)

def migrate():
    print(f"🚀 Inizio migrazione da SQLite ({SQLITE_DB}) a Supabase (Postgres)...")
    
    # 1. Connessione a Postgres (Supabase)
    engine_pg = create_engine(DATABASE_URL)
    SessionPg = sessionmaker(bind=engine_pg)
    session_pg = SessionPg()
    
    # 2. Creazione tabelle su Supabase
    print("📦 Creazione tabelle su Supabase...")
    Base.metadata.create_all(bind=engine_pg)
    
    # 3. Connessione a SQLite
    conn_sl = sqlite3.connect(SQLITE_DB)
    conn_sl.row_factory = sqlite3.Row
    cursor_sl = conn_sl.cursor()
    
    tables = [
        ("category_governance", CategoryGovernance),
        ("category_rules", CategoryRule),
        ("products", Product),
        ("harvester_settings", HarvesterSetting),
        ("global_tag_governance", GlobalTagGovernance),
        ("settings", Setting)
    ]
    
    for table_name, model in tables:
        print(f"🔄 Migrazione tabella: {table_name}...")
        cursor_sl.execute(f"SELECT * FROM {table_name}")
        rows = cursor_sl.fetchall()
        
        if not rows:
            print(f"   ℹ️ Tabella {table_name} vuota, salto.")
            continue
            
        print(f"   📥 Trovate {len(rows)} righe. Caricamento...")
        
        # Pulizia tabella destinazione per evitare duplicati in caso di re-run
        session_pg.execute(text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE"))
        
        for row in rows:
            data = dict(row)
            # Gestione Enum per Product.status (Postgres è più rigido)
            if table_name == "products" and data.get("status"):
                # SQLite salva l'enum come stringa, va bene così per SQLAlchemy
                pass
            
            obj = model(**data)
            session_pg.add(obj)
            
        session_pg.commit()
        print(f"   ✅ Tabella {table_name} completata.")

    print("\n✨ MIGRAZIONE COMPLETATA CON SUCCESSO! ✨")
    print("Ora puoi aggiornare database.py per puntare a Supabase.")
    
    session_pg.close()
    conn_sl.close()

if __name__ == "__main__":
    migrate()
