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

    async def refresh_token(self):
        """Rigenera l'Access Token usando Client ID e Secret (Grant: client_credentials)."""
        from auth_manager import get_raw_api_key, save_api_key
        client_id = get_raw_api_key("shopify_client_id")
        client_secret = get_raw_api_key("shopify_client_secret")
        
        if not client_id or not client_secret or not self.url:
            add_bridge_log("❌ Impossibile rigenerare token: Credenziali Client mancanti.")
            return False

        add_bridge_log("⚠️ Token attuale invalido (401). Tentativo di rigenerazione via Client Credentials...")
        
        clean_url = self.url.replace('https://', '').replace('http://', '').rstrip('/')
        refresh_url = f"https://{clean_url}/admin/oauth/access_token"
        
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(refresh_url, json=payload)
                if resp.status_code == 200:
                    new_token = resp.json().get("access_token")
                    if new_token:
                        self.token = new_token
                        save_api_key("shopify_token", new_token)
                        add_bridge_log("✅ Access Token ottenuto con successo via Client Credentials.")
                        return True
                add_bridge_log(f"❌ Fallimento rigenerazione token ({resp.status_code}): {resp.text}")
                return False
            except Exception as e:
                add_bridge_log(f"❌ Eccezione durante rigenerazione token: {str(e)}")
                return False

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
        """Recupero catalogo Shopify + Merge (Priorità ai locali 'Ready')."""
        local_results = []
        shopify_results = []
        
        db = SessionLocal()
        try:
            # Includiamo sia Ready che MATCHED (prodotti appena associati con foto)
            ready_items = db.query(Product).filter(Product.status.in_([ProductStatus.Ready, ProductStatus.MATCHED])).all()
            for p in ready_items:
                sku = p.sku or f"SKU-{p.id}"
                img = None
                has_images = False
                if p.matched_images_json:
                    try:
                        imgs = json.loads(p.matched_images_json)
                        if imgs: 
                            img = f"/api/drive/proxy/{imgs[0]}"
                            has_images = True
                    except: pass

                local_results.append({
                    "id": f"gid://shopify/Product/Local-{p.id}",
                    "title": p.seo_title or f"{p.brand} {p.model}",
                    "sku": sku,
                    "price": str(p.price) if p.price else "0.00",
                    "status": "LOCAL ONLY",
                    "image": img,
                    "inventory": 1,
                    "product_type": p.category or "Scarpe",
                    "is_local_published": False,
                    "has_images": has_images # Indicatore per il frontend
                })
            
            # Ordiniamo local_results: prima quelli con foto (has_images=True)
            local_results.sort(key=lambda x: x["has_images"], reverse=True)
            
        finally: db.close()

        # 2. Recupero prodotti reali da Shopify (Limite aumentato a 250 per sync profonda)
        if self.token and self.api_url:
            query = """
            query {
              products(first: 250) {
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.post(self.api_url, headers=headers, json={"query": query})
                    
                    if resp.status_code == 401:
                        if await self.refresh_token():
                            headers["X-Shopify-Access-Token"] = self.token
                            resp = await client.post(self.api_url, headers=headers, json={"query": query})

                    if resp.status_code == 200:
                        edges = resp.json().get("data", {}).get("products", {}).get("edges", [])
                        existing_skus = {lr["sku"] for lr in local_results}
                        
                        db = SessionLocal()
                        synced_count = 0
                        
                        for edge in edges:
                            node = edge["node"]
                            v = node["variants"]["edges"][0]["node"] if node["variants"]["edges"] else {"sku": "N/A", "price": "0.00"}
                            sku_val = v.get("sku") or ""
                            
                            # Logica di Sincronizzazione Inversa (Shopify -> DB Locale)
                            # Segnamo come Published solo se il prodotto ha almeno un'immagine su Shopify
                            has_shopify_images = node["images"]["edges"] != []
                            
                            target_p = None
                            if sku_val and sku_val.startswith("SKU-"):
                                try:
                                    pid = int(sku_val.replace("SKU-", ""))
                                    target_p = db.query(Product).filter(Product.id == pid).first()
                                except: pass
                            
                            if not target_p and sku_val and sku_val != "N/A":
                                target_p = db.query(Product).filter(Product.sku == sku_val).first()
                            
                            if target_p and has_shopify_images and target_p.status != ProductStatus.Published:
                                target_p.status = ProductStatus.Published
                                synced_count += 1
                                # add_bridge_log(f"🔄 Auto-Sync: {sku_val} rilevato su Shopify. Stato aggiornato a Published.")

                            if sku_val in existing_skus: continue # Evita duplicati nella lista UI
                            
                            img = node["images"]["edges"][0]["node"]["url"] if node["images"]["edges"] else None
                            shopify_results.append({
                                "id": node["id"], "title": node["title"], "sku": sku_val,
                                "price": v["price"], "status": node["status"], "image": img,
                                "inventory": node["totalInventory"], "product_type": node["productType"] or "Scarpe",
                                "is_local_published": True
                            })
                        
                        if synced_count > 0:
                            db.commit()
                            add_bridge_log(f"✅ Sincronizzazione Stati: {synced_count} prodotti marcati come Published (rilevati su Shopify).")
                        db.close()
                except Exception as e:
                    add_bridge_log(f"❌ Errore durante Sync Shopify -> PIM: {str(e)}")

        return local_results + shopify_results

    async def publish_product_to_shopify(self, sku: str) -> bool:
        """
        Pubblicazione 100% REST (Seguendo alla lettera la guida ufficiale):
        Invia Titolo, Descrizione, Prezzo, SKU e Immagine (Base64) in un unico comando.
        """
        db = SessionLocal()
        try:
            if not sku:
                add_bridge_log("❌ Errore: SKU mancante per la pubblicazione.")
                return False
                
            p = db.query(Product).filter(Product.id == int(sku.replace("SKU-", ""))).first() if sku.startswith("SKU-") else db.query(Product).filter(Product.sku == sku).first()

            if not p: return False
            
            # --- DOPPIO CONTROLLO DI SICUREZZA ---
            if p.status == ProductStatus.Published:
                add_bridge_log(f"ℹ️ [Skip] {sku} è già segnato come Pubblicato. Uso il sync per confermare.")
                return True # Consideriamolo successo visto che è già su Shopify

            add_bridge_log(f"🚀 [REST Atomico] Pubblicazione professionale per {sku}...")

            
            # 1. Preparazione Immagini Base64 (TUTTE)
            images_payload = []
            if p.matched_images_json:
                imgs = json.loads(p.matched_images_json)
                add_bridge_log(f"📸 Preparazione di {len(imgs)} immagini per {sku}...")
                for item in imgs:
                    drive_id = item["id"] if isinstance(item, dict) else item
                    b64 = await self._get_drive_image_base64(drive_id)

                    if b64:
                        images_payload.append({
                            "attachment": b64,
                            "filename": f"img_{drive_id}.jpg"
                        })

            # 2. Costruzione Payload REST Atomico
            if not images_payload:
                add_bridge_log(f"⚠️ [Safety Block] Annullata pubblicazione per {sku}: Nessuna immagine valida recuperata da Drive.")
                return False
                
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
                
                if resp.status_code == 401:
                    if await self.refresh_token():
                        # Riprova con il nuovo token
                        headers["X-Shopify-Access-Token"] = self.token
                        resp = await client.post(rest_url, headers=headers, json=product_payload)
                
                if resp.status_code in [200, 201]:
                    res_json = resp.json()
                    inv_item_id = res_json["product"]["variants"][0]["inventory_item_id"]
                    
                    # Recupero dinamico location (Cerchiamo NAPOLI)
                    loc_url = self.api_url.replace("/graphql.json", "/locations.json")
                    l_resp = await client.get(loc_url, headers=headers)
                    locations = l_resp.json().get("locations", [])
                    
                    target_loc_id = None
                    if locations:
                        # Cerchiamo la sede di Napoli nel nome
                        for loc in locations:
                            if "napoli" in loc.get("name", "").lower():
                                target_loc_id = loc["id"]
                                break
                        
                        # Se non troviamo "Napoli", usiamo la prima disponibile
                        if not target_loc_id:
                            target_loc_id = locations[0]["id"]
                            add_bridge_log(f"⚠️ Sede 'Napoli' non trovata. Uso la location predefinita: {locations[0].get('name')}")
                        else:
                            add_bridge_log(f"📍 Sede Napoli individuata (ID: {target_loc_id})")

                        # 1. Connessione della variante alla location (necessario se 'unflaggato')
                        connect_url = self.api_url.replace("/graphql.json", "/inventory_levels/connect.json")
                        await client.post(connect_url, headers=headers, json={
                            "location_id": target_loc_id,
                            "inventory_item_id": inv_item_id,
                            "relocate_if_necessary": True
                        })
                        add_bridge_log(f"🔗 Sede {target_loc_id} connessa alla variante.")

                        # 2. Set Inventory level (Set a 1 come richiesto)
                        inv_set_url = self.api_url.replace("/graphql.json", "/inventory_levels/set.json")
                        await client.post(inv_set_url, headers=headers, json={
                            "location_id": target_loc_id,
                            "inventory_item_id": inv_item_id,
                            "available": 1
                        })
                        add_bridge_log(f"✅ Giacenza 1 impostata per la sede di Napoli(i).")

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
            
            creds = get_credentials()
            if not creds:
                add_bridge_log(f"❌ Drive Error: Credenziali mancanti per file {file_id}")
                return None
                
            drive_service = build('drive', 'v3', credentials=creds)
            content = drive_service.files().get_media(fileId=file_id).execute()
            
            if not content:
                add_bridge_log(f"❌ Drive Error: Contenuto vuoto per file {file_id}")
                return None
                
            return base64.b64encode(content).decode('utf-8')
        except Exception as e:
            add_bridge_log(f"❌ Drive Exception per {file_id}: {str(e)}")
            return None
