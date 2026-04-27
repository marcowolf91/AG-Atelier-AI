import sqlite3
import os

db_path = 'atelier_ai.db'

def is_garbage(v):
    if v is None: return True
    s = str(v).strip().lower()
    # Lista estesa di "immondizia" AI
    if s in ["", "null", "none", "n/a", "undefined", "unknown", "nan", "-", "none.", "n/d", "."]:
        return True
    if len(s) < 2: return True
    return False

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🧹 Avvio Bonifica Profonda Database...")
    
    fields = ["seo_title", "material", "dimensions", "ai_description_it", "tags"]
    cursor.execute("SELECT id, " + ", ".join(fields) + " FROM products")
    rows = cursor.fetchall()
    
    total_cleaned = 0
    for row in rows:
        pid = row[0]
        updates = []
        for i, field in enumerate(fields):
            val = row[i+1]
            if is_garbage(val):
                updates.append((field, None)) # Forza a NULL reale in DB
        
        if updates:
            set_clause = ", ".join([f"{u[0]} = ?" for u in updates])
            params = [u[1] for u in updates] + [pid]
            cursor.execute(f"UPDATE products SET {set_clause} WHERE id = ?", params)
            total_cleaned += 1
            
    conn.commit()
    conn.close()
    print(f"✅ Bonifica completata. {total_cleaned} prodotti ripuliti da valori sporchi.")
else:
    print("❌ Errore: Database non trovato.")
