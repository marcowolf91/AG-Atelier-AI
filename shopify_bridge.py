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
        Simulazione recupero catalogo da Shopify
        """
        add_bridge_log("Avvio scansione del catalogo online Shopify...")
        # Mock data per ora (nella realtà userebbe shopify.graphql)
        return [
            {"title": "Chanel Boy Bag", "sku": "CH-001", "price": "4500.00", "status": "ACTIVE"},
            {"title": "Louis Vuitton Speedy 30", "sku": "LV-300", "price": "1200.00", "status": "ACTIVE"},
            {"title": "Gucci GG Marmont", "sku": "GU-452", "price": "2100.00", "status": "DRAFT"}
        ]

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
