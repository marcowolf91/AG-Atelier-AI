import asyncio
import json
import logging
import httpx
from database import SessionLocal, Product, ProductStatus
from auth_manager import get_api_key, get_raw_api_key

# Modulo globale per conservare i log live usati dalla UI
LIVE_LOGS = []

def add_log(message: str):
    LIVE_LOGS.append(message)
    if len(LIVE_LOGS) > 50:
        LIVE_LOGS.pop(0)
    with open("harvester_debug.log", "a") as f:
        f.write(f"{datetime.datetime.now()} - {message}\n")

import datetime

class HarvesterEngine:
    def __init__(self):
        self.serper_key = get_raw_api_key("serper")
        self.openai_key = get_raw_api_key("openai")

    async def get_preview(self, item: Product, action: str = "all"):
        """Genera un'anteprima dei dati senza salvarli nel DB."""
        result = {
            "seo_title": item.seo_title or f"{item.brand} {item.model}",
            "tags": item.tags or "Luxury, Authentic, Handbag",
            "ai_description_it": item.ai_description_it or f"Splendido pezzo da collezione firmato {item.brand}.",
            "images": json.loads(item.matched_images_json) if item.matched_images_json else [],
            "material": item.material or "-",
            "dimensions": item.dimensions or "-"
        }
        
        if action in ['seo', 'tags', 'desc', 'all']:
            combined = item.raw_harvested_data
            if not combined:
                query = f"{item.brand} {item.model} luxury product details"
                async with httpx.AsyncClient() as client:
                    try:
                        headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
                        resp = await client.post('https://google.serper.dev/search', headers=headers, json={"q": query, "num":3})
                        res_data = resp.json()
                        snippets = [o['snippet'] for o in res_data.get('organic', [])]
                        combined = "\n".join(snippets)
                    except: combined = f"{item.brand} {item.model} luxury item"
            
            if self.openai_key:
                from openai import AsyncOpenAI
                try:
                    ai_client = AsyncOpenAI(api_key=self.openai_key)
                    prompt = (
                        f"Analyze these details for a {item.brand} product:\n{combined}\n\n"
                        "Return ONLY valid JSON with: 'seo_title', 'tags', 'ai_description_it'."
                    )
                    ai_resp = await ai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                    ai_data = json.loads(ai_resp.choices[0].message.content)
                    result["seo_title"] = ai_data.get("seo_title", result["seo_title"])
                    result["tags"] = ai_data.get("tags", result["tags"])
                    result["ai_description_it"] = ai_data.get("ai_description_it", result["ai_description_it"])
                except Exception as e:
                    add_log(f"⚠ Errore AI Preview: {str(e)}")
        
        return result

    async def run_harvester(self, ids=None):
        """Esegue l'arricchimento in batch sui prodotti."""
        db = SessionLocal()
        try:
            if ids and len(ids) > 0:
                # Arricchimento mirato su selezione
                draft_products = db.query(Product).filter(Product.id.in_(ids)).all()
            else:
                # Arricchimento totale su coda rimanente (senza SEO Title)
                draft_products = db.query(Product).filter(
                    (Product.seo_title == None) | (Product.status == ProductStatus.Error)
                ).all()
            
            if not draft_products:
                add_log("[Harvester] Nessun prodotto pronto per l'arricchimento.")
                return

            add_log(f"🚀 [Harvester] Avvio sessione per {len(draft_products)} prodotti.")
            
            # Primo passaggio: Marcatura immediata per la UI
            for item in draft_products:
                item.is_ai_processing = 1
                item.last_ai_error = None
            db.commit()

            # Secondo passaggio: Elaborazione controllata (Efficienza Locale = 1 alla volta)
            semaphore = asyncio.Semaphore(1) 
            
            async def sem_task(pid):
                async with semaphore:
                    await self._enrich_single_product(pid)
            
            tasks = [sem_task(item.id) for item in draft_products]
            await asyncio.gather(*tasks)
                
            add_log("🏁 [Harvester] Sessione batch terminata.")
        except Exception as e:
            add_log(f"💥 [Critical Error] Harvester Batch: {str(e)}")
        finally:
            db.close()

    async def _enrich_single_product(self, product_id: int):
        db = SessionLocal()
        item = db.query(Product).filter(Product.id == product_id).first()
        if not item:
            db.close()
            return

        try:
            add_log(f"🔍 [Harvester] Elaborazione {item.brand} {item.model}...")
            query = f"{item.brand} {item.model} luxury product features material size"
            snippets = []
            
            # 1. Search (Serper)
            if self.serper_key and not self.serper_key.startswith("***"):
                headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post('https://google.serper.dev/search', headers=headers, json={"q": query})
                    if resp.status_code == 200:
                        snippets = [o['snippet'] for o in resp.json().get('organic', [])]
            
            if not snippets:
                await asyncio.sleep(1)
                snippets = [f"Data for {item.brand} {item.model}"]

            combined_text = "\n".join(snippets)
            item.raw_harvested_data = combined_text
            
            # 2. AI (Priorità Locale via Ollama, Fallback OpenAI)
            resolved_data = {}
            used_local = False
            
            import ollama_bridge
            is_ollama_up = await ollama_bridge.check_ollama_status()
            
            if is_ollama_up:
                add_log(f"🤖 [Harvester] Uso AI Locale (Ollama) per ID {item.id}")
                local_prompt = (
                    "Analizza i dati del prodotto e restituisci SOLO un oggetto JSON.\n"
                    "I TAG devono essere verticali per e-commerce (Shopify Smart Collections).\n"
                    "Priorità tag: Genere (Donna/Uomo), Tipo Articolo (es. Borsa, Giacca, Sneakers), Materiale, Stile (es. Vintage, Moderno).\n"
                    "Esegui l'output in questo formato JSON: "
                    "{'seo_title': '...', 'material': '...', 'dimensions': '...', 'tags': 'almeno 5 tag verticali in ITALIANO separati da virgola'}\n"
                    f"Dati Prodotto: {combined_text}"
                )
                local_resp = await ollama_bridge.generate_narrative("llama3", local_prompt)
                try:
                    # Estrazione JSON robusta
                    clean_json = local_resp.strip()
                    try:
                        start_idx = clean_json.find('{')
                        end_idx = clean_json.rfind('}') + 1
                        if start_idx >= 0 and end_idx > start_idx:
                            clean_json = clean_json[start_idx:end_idx]
                        
                        resolved_data = json.loads(clean_json)
                        used_local = True
                    except Exception as e:
                        add_log(f"⚠ Errore parsing AI Locale per ID {item.id}: {str(e)}")
                        add_log(f"Contenuto grezzo: {clean_json[:100]}...")
                except Exception as e:
                    add_log(f"⚠ Errore critico AI Locale: {str(e)}")

            if not used_local and self.openai_key and not self.openai_key.startswith("***"):
                add_log(f"☁ [Harvester] Uso OpenAI Cloud per ID {item.id}")
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=self.openai_key)
                prompt = (
                    "Based on these snippets, provide a JSON with: 'seo_title', 'material', 'dimensions', 'tags'.\n"
                    "IMPORTANT RULES:\n"
                    "1. Use 'Title Case' for ALL strings.\n"
                    "2. 'tags' must be a comma-separated string in ITALIAN.\n"
                    "3. All descriptive fields must be in ITALIAN.\n"
                    "Data:\n" + combined_text
                )
                resp = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}
                )
                resolved_data = json.loads(resp.choices[0].message.content)

            if resolved_data:
                item.seo_title = resolved_data.get("seo_title", item.seo_title)
                item.material = resolved_data.get("material", item.material)
                item.dimensions = resolved_data.get("dimensions", item.dimensions)
                
                # Normalizzazione Tags (gestione stringa o lista)
                raw_tags = resolved_data.get("tags", item.tags)
                if isinstance(raw_tags, list):
                    item.tags = ", ".join([str(t).strip() for t in raw_tags])
                elif isinstance(raw_tags, str):
                    item.tags = raw_tags.strip()
                else:
                    item.tags = str(raw_tags)
            
            item.status = ProductStatus.Ready
            add_log(f"✅ [Harvester] Successo per ID {item.id} ({'Locale' if used_local else 'Cloud'})")

        except Exception as e:
            err_msg = str(e)
            item.last_ai_error = err_msg
            item.status = ProductStatus.Error
            add_log(f"❌ [Harvester Error] ID {item.id}: {err_msg}")
        
        finally:
            item.is_ai_processing = 0
            db.commit()
            db.close()

