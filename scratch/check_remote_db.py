import os
from sqlalchemy import create_engine, text

db_url = "postgresql://postgres.uvhiwrrobqeteeidkatm:nxKL8w1oxLA9WzSg@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
engine = create_engine(db_url)

with engine.connect() as conn:
    print("--- PRODOTTI VALENTINO NEL DB ---")
    query = text("SELECT id, brand, model, material, source_sheet FROM products WHERE brand ILIKE '%VALENTINO%'")
    results = conn.execute(query).fetchall()
    for r in results:
        print(f"ID: {r[0]} | Brand: {r[1]} | Modello: {r[2]} | Mat: {r[3]} | Foglio: {r[4]}")
    
    if not results:
        print("Nessun prodotto Valentino trovato.")
    
    print("\n--- STATO GENERALE ---")
    count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
    print(f"Totale prodotti nel DB: {count}")
