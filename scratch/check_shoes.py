import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(os.getenv('DATABASE_URL'))
with engine.connect() as conn:
    query = "SELECT sku, brand, model, category, dimensions FROM products WHERE category ILIKE '%scarpe%' LIMIT 20"
    res = conn.execute(text(query)).fetchall()
    print("--- RISULTATI SCARPE ---")
    for r in res:
        print(f"SKU: {r.sku} | {r.brand} {r.model} | Categoria: {r.category} | Taglia/Dim: {r.dimensions}")
