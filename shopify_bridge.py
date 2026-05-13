import asyncio
import json
import os
import httpx
from database import SessionLocal, Product, ProductStatus

def add_bridge_log(message: str):
    import datetime
    emoji = "📦"
    if "Errore" in message or "❌" in message: emoji = "⚠️"
    elif "Successo" in message or "✅" in message: emoji = "🚀"
    
    clean_msg = f"{emoji} Shopify: {message}"
    with open("harvester_debug.log", "a") as f:
        f.write(f"{datetime.datetime.now()} - {clean_msg}\n")

class ShopifyBridge:
    def __init__(self):
        from auth_manager import get_raw_api_key
        self.token = get_raw_api_key("shopify_token")
        self.url = get_raw_api_key("shopify_url")
        if self.url:
            self.api_url = f"https://{self.url.replace('https://', '').replace('http://', '').rstrip('/')}/admin/api/2026-04/graphql.json"
        else:
            self.api_url = None

    async def check_connection(self):
        if not self.token or not self.api_url: return False, "Credenziali mancanti"
        query = "{ shop { name } }"
        headers = {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(self.api_url, json={"query": query}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" in data: return False, data['errors'][0].get('message')
                    return True, data["data"]["shop"]["name"]
                return False, f"HTTP {resp.status_code}"
            except Exception as e: return False, str(e)

    async def sync_catalog_with_shopify(self):
        """Recupero catalogo Shopify + Merge con i prodotti locali 'Ready'."""
        results = []
        # 1. Recupero prodotti reali da Shopify
        if self.token and self.api_url:
            query = """
            query {
              products(first: 50) {
                edges {
                  node {
                    id title status totalInventory productType
                    variants(first: 1) { edges { node { sku price } } }
                    images(first: 1) { edges { node { url } } }
                  }
                }
              }
            }
            """
            headers = {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=20.0) as client:
                try:
                    resp = await client.post(self.api_url, json={"query": query}, headers=headers)
                    if resp.status_code == 200:
                        edges = resp.json().get("data", {}).get("products", {}).get("edges", [])
                        for edge in edges:
                            node = edge["node"]
                            v = node["variants"]["edges"][0]["node"] if node["variants"]["edges"] else {"sku": "N/A", "price": "0.00"}
                            img = node["images"]["edges"][0]["node"]["url"] if node["images"]["edges"] else None
                            results.append({
                                "id": node["id"], "title": node["title"], "sku": v["sku"],
                                "price": v["price"], "status": node["status"], "image": img,
                                "inventory": node["totalInventory"], "product_type": node["productType"] or "Scarpe",
                                "is_local_published": True
                            })
                except: pass

        # 2. Aggiunta prodotti locali pronti per la pubblicazione
        db = SessionLocal()
        try:
            existing_skus = {r["sku"] for r in results if r.get("sku")}
            ready_items = db.query(Product).filter(Product.status == ProductStatus.Ready).all()
            for p in ready_items:
                sku = p.sku or f"SKU-{p.id}"
                if sku in existing_skus: continue
                
                img = None
                if p.matched_images_json:
                    try:
                        imgs = json.loads(p.matched_images_json)
                        if imgs: img = f"/api/drive/proxy/{imgs[0]}"
                    except: pass

                results.append({
                    "id": f"gid://shopify/Product/Local-{p.id}",
                    "title": p.seo_title or f"{p.brand} {p.model}",
                    "sku": sku,
                    "price": str(p.price) if p.price else "0.00",
                    "status": "LOCAL ONLY",
                    "image": img,
                    "inventory": 1,
                    "product_type": p.category or "Scarpe",
                    "is_local_published": False
                })
        finally: db.close()
        return results

    async def publish_product_to_shopify(self, sku: str) -> bool:
        """
        Pubblicazione 100% REST (Seguendo alla lettera la guida ufficiale):
        Invia Titolo, Descrizione, Prezzo, SKU e Immagine (Base64) in un unico comando.
        """
        db = SessionLocal()
        try:
            p = db.query(Product).filter(Product.id == int(sku.replace("SKU-", ""))).first() if sku.startswith("SKU-") else db.query(Product).filter(Product.sku == sku).first()
            if not p: return False
            
            add_bridge_log(f"🚀 [REST Atomico] Pubblicazione professionale per {sku}...")
            
            # 1. Preparazione Immagini Base64 (TUTTE)
            images_payload = []
            if p.matched_images_json:
                imgs = json.loads(p.matched_images_json)
                add_bridge_log(f"📸 Preparazione di {len(imgs)} immagini per {sku}...")
                for drive_id in imgs:
                    b64 = await self._get_drive_image_base64(drive_id)
                    if b64:
                        images_payload.append({
                            "attachment": b64,
                            "filename": f"img_{drive_id}.jpg"
                        })

            # 2. Costruzione Payload REST Atomico
            rest_url = self.api_url.replace("/graphql.json", "/products.json")
            product_payload = {
                "product": {
                    "title": p.seo_title or f"{p.brand} {p.model}",
                    "body_html": p.ai_description_it or p.description or "",
                    "vendor": p.brand or "Atelier Lab",
                    "product_type": p.category or "Scarpe",
                    "status": "draft",
                    "tags": p.tags if p.tags else "",
                    "variants": [{
                        "price": f"{float(p.price):.2f}" if p.price else "0.00",
                        "sku": sku,
                        "inventory_management": "shopify"
                    }],
                    "images": images_payload
                }
            }

            # 3. Invio e Sincronizzazione Inventario
            async with httpx.AsyncClient(timeout=120.0) as client:
                headers = {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"}
                resp = await client.post(rest_url, headers=headers, json=product_payload)
                
                if resp.status_code in [200, 201]:
                    res_json = resp.json()
                    inv_item_id = res_json["product"]["variants"][0]["inventory_item_id"]
                    
                    # Recupero dinamico location e set scorta
                    loc_url = self.api_url.replace("/graphql.json", "/locations.json")
                    l_resp = await client.get(loc_url, headers=headers)
                    locations = l_resp.json().get("locations", [])
                    if locations:
                        loc_id = locations[0]["id"]
                        inv_set_url = self.api_url.replace("/graphql.json", "/inventory_levels/set.json")
                        await client.post(inv_set_url, headers=headers, json={
                            "location_id": loc_id,
                            "inventory_item_id": inv_item_id,
                            "available": 1
                        })
                        add_bridge_log(f"✅ Inventario impostato a 1 sulla location {loc_id}")

                    p.status = ProductStatus.Published
                    db.commit()
                    add_bridge_log(f"✅ Successo REST Totale: {sku} online con {len(images_payload)} foto.")
                    return True
                else:
                    add_bridge_log(f"❌ Errore REST ({resp.status_code}): {resp.text}")
                    return False

        except Exception as e:
            add_bridge_log(f"❌ Eccezione REST: {str(e)}")
            return False
        finally:
            db.close()

    async def _get_drive_image_base64(self, file_id):
        try:
            from google_auth import get_credentials
            from googleapiclient.discovery import build
            import base64
            drive_service = build('drive', 'v3', credentials=get_credentials())
            content = drive_service.files().get_media(fileId=file_id).execute()
            return base64.b64encode(content).decode('utf-8')
        except: return None
