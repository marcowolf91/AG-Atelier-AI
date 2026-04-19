from database import SessionLocal, Product, ProductStatus

def seed_data():
    db = SessionLocal()
    
    # Controlla se esistono già dati
    if db.query(Product).first():
        print("Il database contiene già dei dati. Seed cancellato.")
        db.close()
        return

    sample_products = [
        Product(
            sku="AT-001",
            brand="Chanel",
            model="Classic Flap Bag Medium",
            category="Bags",
            status=ProductStatus.Published,
            condition_grade="Pristine",
            material="Caviar Leather",
            color="Black",
            hardware_type="Gold",
            original_sheets_row=2,
            drive_folder_url="https://drive.google.com/...",
            ai_description_it="Un'icona intramontabile di stile. La Classic Flap in pelle Caviar nera con hardware dorato è l'apice dell'eleganza parigina.",
            api_cost_usd=0.002
        ),
        Product(
            sku="AT-002",
            brand="Hermès",
            model="Birkin 30",
            category="Bags",
            status=ProductStatus.Validating,
            condition_grade="Excellent",
            material="Togo Leather",
            color="Etoupe",
            hardware_type="Palladium",
            original_sheets_row=3,
            drive_folder_url="https://drive.google.com/...",
            ai_description_it="La leggendaria Birkin 30 in Togo color Etoupe, perfetta sintesi di esclusività artigianale."
        ),
        Product(
            sku="AT-003",
            brand="Rolex",
            model="Daytona Cosmograph",
            category="Watches",
            status=ProductStatus.Draft,
            condition_grade="Good",
            material="Steel",
            color="White",
            hardware_type="Steel",
            original_sheets_row=4,
            drive_folder_url="",
            ai_description_it="",
            tags="vintage, chronometre, steal, sport",
            image_match_score=0.0,
            api_cost_usd=0.0
        ),
        Product(
            sku="AT-004",
            brand="Cartier",
            model="Love Bracelet",
            category="Jewelry",
            status=ProductStatus.Error,
            condition_grade="Excellent",
            material="Yellow Gold 18k",
            color="Gold",
            hardware_type="Gold",
            original_sheets_row=5,
            drive_folder_url="",
            ai_description_it="", # Manca la foto
            tags="",
            image_match_score=0.0,
            api_cost_usd=0.0
        ),
        
        # Un item extra per testare the muse
        Product(
            sku="AT-005",
            brand="Prada",
            model="Re-Edition 2005",
            category="Bags",
            status=ProductStatus.Processing,
            condition_grade="Pristine",
            material="Nylon",
            color="Black",
            hardware_type="Silver",
            original_sheets_row=6,
            drive_folder_url="https://drive.google.com/...",
            ai_description_it="", # vuoto così the muse può generarla
            tags="re-edition, 2005, nylon, saffiano trimmings",
            image_match_score=98.5,
            api_cost_usd=0.001
        ),
    ]
    
    db.add_all(sample_products)
    db.commit()
    db.close()
    
    print("Dati di test inseriti correttamente.")

if __name__ == "__main__":
    seed_data()
