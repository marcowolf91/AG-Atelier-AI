import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(os.getenv('DATABASE_URL'))
with engine.connect() as conn:
    res = conn.execute(text("SELECT * FROM settings WHERE service_name = 'serper'")).fetchone()
    if res:
        print(f"✅ Trovata chiave per Serper: {res}")
    else:
        print("❌ Chiave Serper NON trovata.")
