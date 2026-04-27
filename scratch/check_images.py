import os
import json
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(os.getenv('DATABASE_URL'))
with engine.connect() as conn:
    query = "SELECT brand, model, matched_images_json FROM products WHERE model ILIKE '%mocassino%' AND brand ILIKE '%gucci%' LIMIT 1"
    res = conn.execute(text(query)).fetchone()
    if res:
        print(f"PRODOTTO: {res.brand} {res.model}")
        imgs = json.loads(res.matched_images_json) if res.matched_images_json else []
        print(f"IMMAGINI TROVATE ({len(imgs)}): {imgs}")
    else:
        print("Prodotto non trovato.")
