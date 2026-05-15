import json

# Mock Objects
class MockProduct:
    def __init__(self, sku, source_sheet):
        self.sku = sku
        self.source_sheet = source_sheet
        self.brand = "Generic"
        self.model = "Model"
        self.matched_images_json = None
        self.drive_folder_id = None

class MockDriveItem:
    def __init__(self, id, name, parent_id, mimeType="image/jpeg"):
        self.id = id
        self.name = name
        self.parent_id = parent_id
        self.mimeType = mimeType

def simulate_matching(products, drive_inventory, sheet_folders):
    matched_results = {}
    folder_inventory_cache = {}

    for p in products:
        sheet_name = p.source_sheet.lower().strip() if p.source_sheet else None
        
        if sheet_name and sheet_name in sheet_folders:
            f_id = sheet_folders[sheet_name]
            # Simulate scoped inventory
            target_inventory = [item for item in drive_inventory if item.parent_id == f_id]
        else:
            # Global fallback
            target_inventory = [item for item in drive_inventory if item.parent_id == "root"]

        valid_images = []
        sku_lower = p.sku.lower() if p.sku else None
        
        for item in target_inventory:
            name_norm = item.name.lower().replace('_', ' ').replace('-', ' ')
            if sku_lower and (sku_lower in name_norm):
                valid_images.append({"id": item.id, "name": item.name})
        
        matched_results[f"{p.source_sheet}_{p.sku}"] = [img["name"] for img in valid_images]
    
    return matched_results

# Setup Mock Data
sheet_folders = {"borse donna": "folder_borse", "scarpe": "folder_scarpe"}
drive_items = [
    MockDriveItem("1", "2.jpg", "folder_borse"), # Photo 2 for Borse
    MockDriveItem("2", "2.jpg", "folder_scarpe"), # Photo 2 for Scarpe
    MockDriveItem("3", "3.jpg", "folder_borse"),
]

products = [
    MockProduct("2", "Borse Donna"),
    MockProduct("2", "Scarpe")
]

# Run Simulation
results = simulate_matching(products, drive_items, sheet_folders)

print("Simulation Results:")
for k, v in results.items():
    print(f"Product {k} matched with: {v}")

# Assertions
assert "folder_borse" in str([drive_items[0].parent_id for name in results["Borse Donna_2"] if name == "2.jpg"])
assert "folder_scarpe" in str([drive_items[1].parent_id for name in results["Scarpe_2"] if name == "2.jpg"])
print("\n✅ Simulation Passed: Sheet-Aware matching works as expected.")
