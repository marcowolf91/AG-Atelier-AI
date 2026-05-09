import sqlite3

def migrate():
    conn = sqlite3.connect('atelier_ai.db')
    cursor = conn.cursor()
    columns_to_add = [
        ("size", "TEXT"),
        ("fit", "TEXT"),
        ("handle_drop", "TEXT"),
        ("accessories_included", "TEXT"),
        ("location", "TEXT")
    ]
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column {col_name} already exists")
            else:
                print(f"Error adding {col_name}: {e}")
    conn.commit()
    conn.close()

if __name__ == '__main__':
    migrate()
