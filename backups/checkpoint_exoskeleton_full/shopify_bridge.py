import asyncio
import logging
import random
from database import SessionLocal, Product, ProductStatus
from auth_manager import get_api_key, get_raw_api_key

# Modulo globale log
BRIDGE_LOGS = []

def add_bridge_log(msg: str):
    BRIDGE_LOGS.append(msg)
    if len(BRIDGE_LOGS) > 50:
        BRIDGE_LOGS.pop(0)

class ShopifyBridge:
    def __init__(self):
        self.shopify_token = get_raw_api_key("shopify_token")
        self.shopify_url = get_raw_api_key("shopify_url")
    
    async def fetch_shopify_catalog(self):
        """Recupera l'elenco prodotti corrente da Shopify per la sincronizzazione bilaterale."""
        if not self.shopify_token or not self.shopify_url:
            return {"error": "Configurazione Shopify mancante"}
            
        add_bridge_log("📡 [Shopify Mirror] Recupero catalogo in corso...")
        
        try:
            # Simulazione chiamata GraphQL (get products)
            # In produzione useremmo httpx.post(self.shopify_url, headers=...)
            await asyncio.sleep(1.2)
            
            # Simulazione dati Shopify (Mock) - In un caso reale questi dati verrebbero dal JSON di risposta
            # Mostriamo alcuni prodotti finti che l'utente potrebbe avere su Shopify
            mock_products = [
                {"id": "gid://shopify/Product/1", "title": "LOUIS VUITTON Keepall 50", "sku": "LV-KP-001", "price": "1200.00", "status": "ACTIVE"},
                {"id": "gid://shopify/Product/2", "title": "CHANEL Boy Bag Medium", "sku": "CH-BB-99", "price": "4500.00", "status": "ACTIVE"},
                {"id": "gid://shopify/Product/3", "title": "HERMES Birkin 30 Gold", "sku": "H-B30-G", "price": "15000.00", "status": "DRAFT"}
            ]
            
            add_bridge_log(f"✅ [Shopify Mirror] {len(mock_products)} prodotti rilevati online.")
            return {"status": "ok", "products": mock_products}
            
        except Exception as e:
            add_bridge_log(f"❌ Errore sincronizzazione inversa: {str(e)}")
            return {"error": str(e)}

    async def import_product_to_pim(self, shopify_data: dict):
        """Importa un prodotto da Shopify verso il PIM locale."""
        db = SessionLocal()
        try:
            sku = shopify_data.get("sku")
            if not sku:
                return {"error": "Lo SKU è obbligatorio per l'importazione"}
                
            # Controllo duplicati
            exists = db.query(Product).filter(Product.sku == sku).first()
            if exists:
                return {"status": "exists", "message": f"Prodotto con SKU {sku} già presente nel PIM"}
                
            # Creazione nuovo prodotto
            new_prod = Product(
                brand=shopify_data.get("title", "").split()[0].upper(), # Estrazione brand mock
                model=shopify_data.get("title", ""),
                sku=sku,
                price=float(shopify_data.get("price", 0)),
                status=ProductStatus.Draft,
                source_sheet="Shopify Import"
            )
            db.add(new_prod)
            db.commit()
            add_bridge_log(f"📥 [Import] Prodotto {sku} importato con successo da Shopify.")
            return {"status": "ok", "id": new_prod.id}
        except Exception as e:
            db.rollback()
            return {"error": str(e)}
        finally:
            db.close()

    async def publish_ready_items(self):
        """Prende tutti i prodotti Ready e cerca di caricarli su Shopify."""
        db = SessionLocal()
        ready_items = db.query(Product).filter(Product.status == ProductStatus.Ready).all()
        
        if not ready_items:
            add_bridge_log("Nessun articolo 'Ready' trovato per la pubblicazione.")
            db.close()
            return {"status": "empty", "published": 0}
            
        published_count = 0
        
        for item in ready_items:
            add_bridge_log(f"Inizio pubblicazione: {item.brand} {item.model}")
            
            # 1. API Call Shopify con Retry System logic
            success = await self._upload_to_shopify_with_retry(item)
            
            if success:
                # 2. Sync Inverso su Google Sheets
                await self._reverse_sync_to_sheets(item.original_sheets_row, "https://storedemo.com/products/" + item.sku)
                
                # 3. Stato locale -> Published
                item.status = ProductStatus.Published
                db.commit()
                published_count += 1
                add_bridge_log(f"✅ Successo: {item.sku} online.")
            else:
                item.status = ProductStatus.Error
                db.commit()
                add_bridge_log(f"❌ Errore permanente per {item.sku}. Status -> Error")
                
        db.close()
        return {"status": "ok", "published": published_count}
        
    async def _upload_to_shopify_with_retry(self, item: Product, max_retries=3):
        """
        Simula caricamento asincrono usando REST/GraphQL verso Shopify.
        Include Metafields, HTML Body e Upload Seriale Immagini.
        """
        for attempt in range(1, max_retries + 1):
            try:
                # Simulazione latenza rete (1.5 secondi)
                await asyncio.sleep(1.5)
                
                # Payload GraphQL Admin API (productSet) in Synchronous mode
                payload = {
                    "query": '''
                    mutation productSet($synchronous: Boolean!, $input: ProductSetInput!) {
                        productSet(synchronous: $synchronous, input: $input) {
                            product { id title }
                            userErrors { field message }
                        }
                    }
                    ''',
                    "variables": {
                        "synchronous": True,
                        "input": {
                            "title": f"Pre-Owned {item.brand} {item.model}",
                            "descriptionHtml": item.ai_description_it,
                            "vendor": item.brand,
                            "productType": "Luxury Handbag",
                            "status": "ACTIVE",
                            "metafields": [
                                {"namespace": "luxury", "key": "material", "value": item.material},
                                {"namespace": "luxury", "key": "hardware", "value": item.hardware_type},
                                {"namespace": "luxury", "key": "condition", "value": item.condition_grade},
                                {"namespace": "luxury", "key": "dimensions", "value": item.dimensions}
                            ]
                        }
                    }
                }
                
                # Finto errore per testare i retry
                if attempt == 1 and random.random() < 0.2:
                    raise Exception("Shopify API Rate Limit Reached (429)")

                # Finto Media Handler (Upload Foto 1..5)
                await self._upload_media(item.drive_folder_url)

                return True # Completato con successo

            except Exception as e:
                add_bridge_log(f"Errore su {item.sku} (Tentativo {attempt}/{max_retries}): {str(e)}")
                if attempt == max_retries:
                    return False
                await asyncio.sleep(2) # Backoff
                
    async def _upload_media(self, folder_url):
        # Scarica drive url e invia a stage_uploads Shopify (mock)
        await asyncio.sleep(1)
        
    async def _reverse_sync_to_sheets(self, row_index, generated_url):
        # Google Sheets Update
        # Scrive l'URL del nuovo prodotto nella riga originaria
        await asyncio.sleep(1)
