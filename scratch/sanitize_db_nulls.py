import sqlite3
import os

db_path = 'atelier_ai.db'

def sanitize(v):
    if v is None: return None
    s = str(v).strip().lower()
    if s in ["null", "none", "n/a", "undefined", "unknown", "nan"]:
        return None
    return str(v).strip()

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🔍 Avvio sanificazione database...")
    
    # Campi da pulire
    fields = ["seo_title", "material", "dimensions", "ai_description_it", "tags"]
    
    cursor.execute("SELECT id, " + ", ".join(fields) + " FROM products")
    rows = cursor.fetchall()
    
    cleaned_count = 0
    for row in rows:
        pid = row[0]
        updates = []
        for i, field in enumerate(fields):
            original = row[i+1]
            sanitized = sanitize(original)
            if original != sanitized:
                updates.append((field, sanitized))
        
        if updates:
            set_clause = ", ".join([f"{u[0]} = ?" for u in updates])
            params = [u[1] for u in updates] + [pid]
            cursor.execute(f"UPDATE products SET {set_clause} WHERE id = ?", params)
            cleaned_count += 1
            
    conn.commit()
    conn.close()
    print(f"✅ Sanificazione completata. Prodotti puliti: {cleaned_count}")
else:
    print("❌ Database non trovato.")
