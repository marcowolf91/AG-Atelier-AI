import sqlite3
import os

db_path = '/Users/marcosimonelli/Desktop/AG Atelier AI/atelier_ai.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Lista colonne da verificare/aggiungere
    columns = [
        ('tags', 'TEXT'),
        ('seo_title', 'TEXT'),
        ('image_match_score', 'REAL')
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")
            print(f"✅ Colonna '{col_name}' aggiunta.")
        except sqlite3.OperationalError:
            print(f"ℹ️ Colonna '{col_name}' già presente.")
            
    conn.commit()
    conn.close()
    print("Database allineato correttamente.")
else:
    print("Database non ancora creato.")
