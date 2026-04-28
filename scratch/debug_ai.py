
import asyncio
import os
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from harvester import HarvesterEngine
from database import Product

load_dotenv()

async def debug_all():
    engine = HarvesterEngine()
    db_url = os.getenv("DATABASE_URL").replace("postgres://", "postgresql://")
    db_engine = create_engine(db_url)
    Session = sessionmaker(bind=db_engine)
    db = Session()
    
    product_id = 153
    item = db.query(Product).filter(Product.id == product_id).first()
    print(f"🔎 Test su Prodotto: {item.brand} {item.model}")
    
    prompt = (
        f"Analizza questi dati: Brand: {item.brand}, Modello: {item.model}.\n"
        "Restituisci un JSON con il campo 'tags' (lista di stringhe)."
    )

    # Proviamo Gemini
    print("\n--- 🤖 TEST GEMINI ---")
    try:
        res_gemini = await engine.get_ai_json(prompt, "gemini")
        print("RISPOSTA GEMINI:")
        print(json.dumps(res_gemini, indent=2))
    except Exception as e:
        print(f"❌ Errore Gemini: {e}")

    # Proviamo Llama3 locale
    print("\n--- 🦙 TEST LLAMA3 ---")
    try:
        res_llama = await engine.get_ai_json(prompt, "llama3")
        print("RISPOSTA LLAMA3:")
        print(json.dumps(res_llama, indent=2))
    except Exception as e:
        print(f"❌ Errore Llama3: {e}")

    db.close()

if __name__ == "__main__":
    asyncio.run(debug_all())
