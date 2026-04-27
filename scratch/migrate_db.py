import sqlite3
import os

db_path = 'atelier_ai.db'

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Checking for missing columns...")
    
    # Get existing columns
    cursor.execute("PRAGMA table_info(products)")
    columns = [row[1] for row in cursor.fetchall()]
    
    needed = [
        ("has_master_conflict", "INTEGER DEFAULT 0"),
        ("master_snapshot_json", "TEXT")
    ]
    
    for col_name, col_type in needed:
        if col_name not in columns:
            print(f"Adding column {col_name}...")
            try:
                cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")
                print(f"Column {col_name} added successfully.")
            except Exception as e:
                print(f"Error adding {col_name}: {e}")
        else:
            print(f"Column {col_name} already exists.")
            
    conn.commit()
    conn.close()
    print("Migration complete.")
else:
    print("Database file not found. App will create it on start.")
