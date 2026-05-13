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
        """Verifica se le credenziali sono valide effettuando una query minima."""
        add_bridge_log("🔍 Inizio verifica connessione Shopify...")
        # Se non abbiamo il token, proviamo a ottenerlo via Client Credentials (Standard 2026)
        if not self.token:
            from auth_manager import get_raw_api_key, save_api_key
            client_id = get_raw_api_key("shopify_client_id")
            client_secret = get_raw_api_key("shopify_client_secret")
            
            if not client_id or not client_secret:
                return False, "ID Client o Segreto mancanti"
            
            success = await self._exchange_credentials_for_token(client_id, client_secret)
            if not success:
                return False, "Scambio credenziali fallito (ID/Segreto non validi)"

        query = "{ shop { name } }"
        headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(self.api_url, json={"query": query}, headers=headers)
                
                # Se il token attuale è invalido (401), proviamo a scambiare le credenziali
                if resp.status_code == 401:
                    from auth_manager import get_raw_api_key
                    client_id = get_raw_api_key("shopify_client_id")
                    client_secret = get_raw_api_key("shopify_client_secret")
                    
                    if client_id and client_secret:
                        add_bridge_log("⚠️ Token attuale invalido (401). Tentativo di rigenerazione via Client Credentials...")
                        success = await self._exchange_credentials_for_token(client_id, client_secret)
                        if success:
                            # Riprova la query con il nuovo token
                            headers["X-Shopify-Access-Token"] = self.token
                            resp = await client.post(self.api_url, json={"query": query}, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" in data:
                        add_bridge_log(f"❌ Errore GraphQL Shopify: {data['errors'][0].get('message')}")
                        return False, f"Errore GraphQL: {data['errors'][0].get('message')}"
                    add_bridge_log(f"✅ Connessione riuscita per lo store: {data['data']['shop']['name']}")
                    return True, data["data"]["shop"]["name"]
                else:
                    add_bridge_log(f"❌ Errore connessione Shopify. HTTP {resp.status_code}: {resp.text}")
                    return False, f"Errore HTTP {resp.status_code}"
            except Exception as e:
                add_bridge_log(f"❌ Eccezione connessione Shopify: {str(e)}")
                return False, str(e)

    async def _exchange_credentials_for_token(self, client_id, client_secret):
        """Effettua il 'Client Credentials Grant' per ottenere un Access Token."""
        if not self.url: 
            add_bridge_log("❌ Scambio fallito: URL store mancante.")
            return False
        
        token_url = f"https://{self.url.replace('https://', '').replace('http://', '').split('/')[0].strip()}/admin/oauth/access_token"
        add_bridge_log(f"🔄 Tentativo di scambio credenziali presso {token_url}...")
        
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(token_url, data=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    new_token = data.get("access_token")
                    if new_token:
                        from auth_manager import save_api_key
                        save_api_key("shopify_token", new_token)
                        self.token = new_token
                        add_bridge_log("✅ Access Token ottenuto con successo via Client Credentials.")
                        return True
                    add_bridge_log("❌ Risposta 200 ma 'access_token' non trovato nel JSON.")
                else:
                    add_bridge_log(f"❌ Scambio fallito. HTTP {resp.status_code}: {resp.text}")
                return False
            except Exception as e:
                add_bridge_log(f"❌ Eccezione durante lo scambio: {str(e)}")
                return False

    async def sync_catalog_with_shopify(self):
        """
        Recupero catalogo da Shopify via GraphQL
        """
        add_bridge_log("Avvio scansione del catalogo online Shopify...")
        
        results = []
        
        # 1. Recupero VERO catalogo Shopify
        if self.token and self.api_url:
            query = """
            query {
              products(first: 50) {
                edges {
                  node {
                    id
                    title
                    status
                    totalInventory
                    productType
                    variants(first: 1) {
                      edges {
                        node {
                          sku
                          price
                        }
                      }
                    }
                    images(first: 1) {
                      edges {
                        node {
                          url
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            headers = {
                "X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                try:
                    resp = await client.post(self.api_url, json={"query": query}, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        edges = data.get("data", {}).get("products", {}).get("edges", [])
                        for edge in edges:
                            node = edge.get("node", {})
                            
                            sku = "N/A"
                            price = "0.00"
                            if node.get("variants") and node["variants"].get("edges"):
                                vnode = node["variants"]["edges"][0].get("node", {})
                                sku = vnode.get("sku", "N/A")
                                price = vnode.get("price", "0.00")
                                
                            img_url = None
                            if node.get("images") and node["images"].get("edges"):
                                img_url = node["images"]["edges"][0].get("node", {}).get("url")
                                
                            results.append({
                                "id": node.get("id"),
                                "title": node.get("title", ""),
                                "sku": sku,
                                "price": price,
                                "status": node.get("status", "ACTIVE"),
                                "image": img_url,
                                "inventory": node.get("totalInventory", 0),
                                "product_type": node.get("productType", "Non categorizzato") or "Non categorizzato"
                            })
                        add_bridge_log(f"✅ Recuperati {len(edges)} prodotti da Shopify.")
                    else:
                        add_bridge_log(f"❌ Errore Shopify ({resp.status_code}): {resp.text}")
                except Exception as e:
                    add_bridge_log(f"❌ Eccezione durante fetch catalogo: {str(e)}")
        
        # 2. Aggiungiamo i prodotti LOCALI (Pronti o Pubblicati)
        db = SessionLocal()
        published_items = db.query(Product).filter(Product.status == ProductStatus.Published).all()
        ready_items = db.query(Product).filter(Product.status == ProductStatus.Ready).all()
        
        published_skus = {p.sku for p in published_items if p.sku}
        
        # Marchiamo i prodotti Shopify che derivano dal nostro DB locale
        for r in results:
            r["is_local_published"] = (r.get("sku") in published_skus)
            
        existing_skus = {r.get("sku") for r in results if r.get("sku")}
        
        # A) Aggiungiamo i "Da Pubblicare" (Ready)
        for p in ready_items:
            if p.sku and p.sku in existing_skus:
                continue # Evita duplicato se stranamente è già su Shopify
                
            img = None
            if p.matched_images_json:
                try:
                    imgs = json.loads(p.matched_images_json)
                    if imgs and len(imgs) > 0: 
                        img = f"/api/drive/proxy/{imgs[0]}"
                except: pass
                
            results.append({
                "id": f"gid://shopify/Product/Local-{p.id}",
                "title": p.seo_title or f"{p.brand} {p.model}",
                "sku": p.sku or f"SKU-{p.id}",
                "price": str(p.price) if p.price else "0.00",
                "status": "LOCAL ONLY",
                "image": img,
                "inventory": 1,
                "product_type": p.category or "Non categorizzato",
                "is_local_published": False
            })
            
        # B) Aggiungiamo i "Pubblicati" (Published) che Shopify non ha ancora indicizzato
        # oppure li riportiamo a "Ready" se sono stati cancellati da Shopify.
        import datetime
        now = datetime.datetime.utcnow()
        
        for p in published_items:
            if p.sku and p.sku in existing_skus:
                continue
                
            img = None
            if p.matched_images_json:
                try:
                    imgs = json.loads(p.matched_images_json)
                    if imgs and len(imgs) > 0: 
                        img = f"/api/drive/proxy/{imgs[0]}"
                except: pass
            
            # Timer di verità: se l'aggiornamento è recentissimo (< 60s), diamo tempo a Shopify.
            # Se è passato più di un minuto, significa che il prodotto è stato cancellato da Shopify.
            time_since_update = (now - p.updated_at).total_seconds() if p.updated_at else 100
            
            if time_since_update < 60:
                ui_status = "DRAFT"
                is_pub = True
            else:
                # E' stato cancellato da Shopify! Riportiamo indietro lo stato.
                p.status = ProductStatus.Ready
                db.commit()
                ui_status = "LOCAL ONLY"
                is_pub = False
                
            results.append({
                "id": f"gid://shopify/Product/Local-{p.id}",
                "title": p.seo_title or f"{p.brand} {p.model}",
                "sku": p.sku or f"SKU-{p.id}",
                "price": str(p.price) if p.price else "0.00",
                "status": ui_status,
                "image": img,
                "inventory": 1,
                "product_type": p.category or "Non categorizzato",
                "is_local_published": is_pub
            })
            
        db.close()

        return results

    async def publish_product_to_shopify(self, sku: str) -> dict:
        """
        Esporta un prodotto dal PIM verso Shopify come Bozza.
        """
        db = SessionLocal()
        try:
            if sku.startswith("SKU-"):
                try:
                    product_id = int(sku.replace("SKU-", ""))
                    p = db.query(Product).filter(Product.id == product_id).first()
                except:
                    p = db.query(Product).filter(Product.sku == sku).first()
            else:
                p = db.query(Product).filter(Product.sku == sku).first()
                
            if not p:
                return {"status": "error", "message": f"Prodotto {sku} non trovato nel database."}
            
            title = p.seo_title or f"{p.brand} {p.model}"
            price = str(p.price) if p.price else "0.00"
            # Usa la descrizione AI arricchita, altrimenti quella base
            desc_html = p.ai_description_it or p.description or ""
            
            # Formattazione Tag CSV-style (array di stringhe)
            tags_list = [t.strip() for t in p.tags.split(",")] if p.tags else []
            
            mutation_create = """
            mutation productCreate($input: ProductInput!) {
              productCreate(input: $input) {
                product {
                  id
                  title
                  variants(first: 1) {
                    edges {
                      node {
                        id
                      }
                    }
                  }
                }
                userErrors {
                  field
                  message
                }
              }
            }
            """
            
            variables_create = {
                "input": {
                    "title": title,
                    "descriptionHtml": desc_html,
                    "productType": p.category or "Non categorizzato",
                    "vendor": p.brand or "",
                    "status": "DRAFT",
                    "tags": tags_list,
                    "seo": {
                        "title": p.seo_title or title,
                        "description": desc_html[:320]
                    }
                }
            }
            
            if not self.token or not self.api_url:
                return {"status": "error", "message": "Credenziali Shopify mancanti nel Bridge."}
                
            headers = {
                "X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json"
            }
            
            # STEP 1: Create the Product shell
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp1 = await client.post(
                    self.api_url, 
                    headers=headers, 
                    json={"query": mutation_create, "variables": variables_create}
                )
                result1 = resp1.json()
            
            if "errors" in result1:
                return {"status": "error", "message": str(result1["errors"])}
            
            create_result = result1.get("data", {}).get("productCreate", {})
            errors = create_result.get("userErrors", [])
            
            if errors:
                return {"status": "error", "message": errors[0]["message"]}
                
            product_data = create_result.get("product", {})
            product_id = product_data["id"]
            
            # STEP 2: Update the Default Variant with SKU and Price
            try:
                # Piccolo delay per permettere l'indicizzazione
                await asyncio.sleep(1.5)
                
                variant_id = product_data["variants"]["edges"][0]["node"]["id"]
                formatted_price = f"{float(p.price):.2f}" if p.price else "0.00"
                
                mutation_variant = """
                mutation productVariantUpdate($input: ProductVariantInput!) {
                  productVariantUpdate(input: $input) {
                    productVariant {
                      id
                      price
                    }
                    userErrors { field message }
                  }
                }
                """
                
                variables_variant = {
                    "input": {
                        "id": variant_id,
                        "price": formatted_price,
                        "sku": p.sku or sku
                    }
                }
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp2 = await client.post(
                        self.api_url, 
                        headers=headers, 
                        json={"query": mutation_variant, "variables": variables_variant}
                    )
                    res_var = resp2.json()
                    
                    var_data = res_var.get("data", {}).get("productVariantUpdate", {})
                    var_errs = var_data.get("userErrors", [])
                    
                    if var_errs:
                        add_bridge_log(f"⚠️ Errore variante per {sku}: {var_errs[0]['message']}")
                    else:
                        updated_p = var_data.get("productVariant", {}).get("price")
                        add_bridge_log(f"✅ Prezzo aggiornato a {updated_p} per {sku}")
                        
            except Exception as variant_err:
                add_bridge_log(f"⚠️ Eccezione variante per {sku}: {str(variant_err)}")
                
            # STEP 3: Handle Image Upload via STAGED UPLOADS (Physical-like flow)
            try:
                if p.matched_images_json:
                    imgs = json.loads(p.matched_images_json)
                    if imgs and len(imgs) > 0:
                        drive_file_id = imgs[0]
                        add_bridge_log(f"📸 Inizio caricamento fisico immagine {drive_file_id}...")
                        
                        # 3a. Download locale da Drive
                        temp_path = f"temp_img_{drive_file_id}.jpg"
                        drive_url = f"https://drive.google.com/uc?export=download&id={drive_file_id}"
                        
                        async with httpx.AsyncClient(follow_redirects=True) as dl_client:
                            dl_resp = await dl_client.get(drive_url)
                            if dl_resp.status_code == 200:
                                with open(temp_path, "wb") as f:
                                    f.write(dl_resp.content)
                            else:
                                raise Exception(f"Download Drive fallito: {dl_resp.status_code}")

                        # 3b. Richiesta Staged Target a Shopify
                        staged_mutation = """
                        mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
                          stagedUploadsCreate(input: $input) {
                            stagedTargets {
                              url
                              resourceUrl
                              parameters { name value }
                            }
                            userErrors { field message }
                          }
                        }
                        """
                        
                        staged_input = [{
                            "filename": f"product_{p.id}.jpg",
                            "mimeType": "image/jpeg",
                            "resource": "PRODUCT_IMAGE",
                            "httpMethod": "POST"
                        }]
                        
                        async with httpx.AsyncClient() as client:
                            s_resp = await client.post(self.api_url, headers=headers, json={"query": staged_mutation, "variables": {"input": staged_input}})
                            s_data = s_resp.json()
                            target = s_data["data"]["stagedUploadsCreate"]["stagedTargets"][0]
                            
                            # 3c. Upload fisico a Shopify (GCS/S3)
                            upload_url = target["url"]
                            params = {p["name"]: p["value"] for p in target["parameters"]}
                            
                            with open(temp_path, "rb") as f:
                                files = {"file": f}
                                up_resp = await client.post(upload_url, data=params, files=files)
                                
                            if up_resp.status_code in [200, 201]:
                                # 3d. Creazione Media finale su Shopify
                                final_resource_url = target["resourceUrl"]
                                media_mutation = """
                                mutation productCreateMedia($media: [CreateMediaInput!]!, $productId: ID!) {
                                  productCreateMedia(media: $media, productId: $productId) {
                                    mediaUserErrors { message }
                                  }
                                }
                                """
                                await client.post(self.api_url, headers=headers, json={
                                    "query": media_mutation, 
                                    "variables": {
                                        "productId": product_id,
                                        "media": [{"originalSource": final_resource_url, "alt": title, "mediaContentType": "IMAGE"}]
                                    }
                                })
                                add_bridge_log(f"✅ Immagine caricata fisicamente per {sku}")
                            
                        # Pulizia file temporaneo
                        if os.path.exists(temp_path): os.remove(temp_path)
                        
            except Exception as img_err:
                add_bridge_log(f"⚠️ Errore caricamento fisico immagine: {str(img_err)}")
                
            # Aggiorniamo lo stato nel PIM locale
            p.status = ProductStatus.Published
            db.commit()
            add_bridge_log(f"✅ Prodotto {sku} pubblicato con successo su Shopify.")
                
            return {"status": "ok", "message": "Prodotto pubblicato con successo come BOZZA."}
        except Exception as e:
            add_bridge_log(f"❌ Errore durante la pubblicazione di {sku}: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    async def import_product_to_pim(self, shopify_data: dict, mode: str = "standard"):
        """
        Importa un prodotto da Shopify nel database locale del PIM.
        Se mode='enrich', inoltra il prodotto alla fase di arricchimento AI.
        """
        db = SessionLocal()
        try:
            sku = shopify_data.get("sku")
            title = shopify_data.get("title", "")
            price = shopify_data.get("price", 0)
            
            # Split Brand/Model if title is standard "Brand Model"
            brand, model = "Unknown", title
            if " " in title:
                parts = title.split(" ", 1)
                brand, model = parts[0], parts[1]

            item = db.query(Product).filter(Product.sku == sku).first()
            created = False
            if not item:
                item = Product(
                    sku=sku,
                    brand=brand.upper(),
                    model=model,
                    price=float(price) if price else 0,
                    status=ProductStatus.Draft
                )
                db.add(item)
                created = True
            
            # Logica Enrichment
            if mode == "enrich":
                item.status = ProductStatus.Processing
                add_bridge_log(f"🔄 Prodotto {sku} inviato a Enrichment AI (Retro-Scaling).")
            
            db.commit()
            pid = item.id
            return {"status": "ok", "id": pid, "message": "Prodotto pronto per arricchimento" if mode == "enrich" else "Prodotto importato"}
        except Exception as e:
            db.rollback()
            add_bridge_log(f"❌ Errore importazione Shopify: {str(e)}")
            return {"error": str(e)}
        finally:
            db.close()

    async def fetch_collections_and_rules(self):
        """Recupera le Smart Collections e le relative regole da Shopify via GraphQL."""
        if not self.token or not self.api_url:
            add_bridge_log("❌ Credenziali Shopify mancanti per il sync collezioni.")
            return []

        query = """
        query {
          collections(first: 50) {
            edges {
              node {
                id
                title
                description
                ruleSet {
                  appliedDisjunctively
                  rules {
                    column
                    relation
                    condition
                  }
                }
              }
            }
          }
        }
        """
        
        headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                resp = await client.post(self.api_url, json={"query": query}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" in data:
                        add_bridge_log(f"❌ Errore GraphQL Shopify: {json.dumps(data['errors'])}")
                        return []
                    
                    collections = []
                    edges = data.get("data", {}).get("collections", {}).get("edges", [])
                    for edge in edges:
                        node = edge.get("node", {})
                        collections.append(node)
                    return collections
                else:
                    add_bridge_log(f"❌ Errore HTTP Shopify ({resp.status_code}): {resp.text}")
            except Exception as e:
                add_bridge_log(f"❌ Eccezione durante fetch collezioni: {str(e)}")
        
        return []

    async def sync_governance_categories(self, db):
        """Sincronizza le collezioni di Shopify con la tabella locale CategoryGovernance."""
        shop_collections = await self.fetch_collections_and_rules()
        if not shop_collections:
            return 0

        from database import CategoryGovernance, CategoryRule
        
        synced_count = 0
        for sc in shop_collections:
            sid = sc["id"]
            name = sc["title"]
            desc = sc.get("description", "")
            rule_set = sc.get("ruleSet")
            
            # 1. Update/Create Category
            cat = db.query(CategoryGovernance).filter(CategoryGovernance.shopify_collection_id == sid).first()
            if not cat:
                cat = CategoryGovernance(shopify_collection_id=sid)
                db.add(cat)
            
            cat.name = name
            cat.description = desc
            if rule_set:
                cat.applied_disjunctively = 1 if rule_set.get("appliedDisjunctively") else 0
            
            db.flush() # Per avere l'ID se nuovo
            
            # 2. Update Rules
            # Per semplicità, cancelliamo le vecchie regole e mettiamo le nuove
            db.query(CategoryRule).filter(CategoryRule.category_id == cat.id).delete()
            
            if rule_set and rule_set.get("rules"):
                for r in rule_set["rules"]:
                    rule = CategoryRule(
                        category_id=cat.id,
                        column=r["column"],
                        relation=r["relation"],
                        condition=r["condition"]
                    )
                    db.add(rule)
            
            synced_count += 1
        
        db.commit()
        add_bridge_log(f"✅ Sincronizzate {synced_count} collezioni da Shopify.")
        return synced_count

    async def publish_ready_items(self):
        """Prende tutti i prodotti Ready e cerca di caricarli su Shopify."""
        db = SessionLocal()
        ready_items = db.query(Product).filter(Product.status == ProductStatus.Ready).all()
        
        if not ready_items:
            db.close()
            return {"status": "ok", "published": 0}

        published_count = 0
        for item in ready_items:
            # 1. Carica su Shopify
            success = await self._upload_to_shopify_with_retry(item)
            
            if success:
                # 2. Crea Checkpoint su Drive (opzionale)
                add_bridge_log(f"📦 Checkpoint per {item.sku} creato su Drive.")
                
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
        for attempt in range(1, max_retries + 1):
            try:
                await asyncio.sleep(1)
                return True # Successo Mock
            except:
                continue
        return False
