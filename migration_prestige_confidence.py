import sqlite3

def run_migration():
    conn = sqlite3.connect('atelier_ai.db')
    cursor = conn.cursor()
    
    print("Checking for missing column: match_confidence")
    try:
        # Check if column exists
        cursor.execute("PRAGMA table_info(products)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "match_confidence" not in columns:
            print("Adding column 'match_confidence' to 'products' table...")
            cursor.execute("ALTER TABLE products ADD COLUMN match_confidence FLOAT")
            conn.commit()
            print("Success: Column 'match_confidence' added.")
        else:
            print("Column 'match_confidence' already exists.")
            
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
