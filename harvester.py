import asyncio
import json
import logging
import httpx
from database import SessionLocal, Product, ProductStatus, ApiUsage
from auth_manager import get_api_key, get_raw_api_key
import google_auth
from googleapiclient.discovery import build

from logger_utils import add_log, LIVE_LOGS

from harvester_state import ENGINE_STATE, PROCESS_PROGRESS
from logger_utils import add_log, LIVE_LOGS

import datetime

class HarvesterEngine:
    def __init__(self):
        self.serper_key = get_raw_api_key("serper")
        self.openai_key = get_raw_api_key("openai")

    def log_api_hit(self, service: str):
        """Registra un utilizzo di un'API nel database."""
        db = SessionLocal()
        try:
            usage = db.query(ApiUsage).filter(ApiUsage.service_name == service).first()
            if not usage:
                usage = ApiUsage(service_name=service, total_hits=1)
                db.add(usage)
            else:
                usage.total_hits += 1
                usage.last_used = datetime.datetime.utcnow()
            db.commit()
        except: pass
        finally: db.close()

    @staticmethod
    def calculate_integrity(item):
        """Calcola il punteggio di integrità Shopify-Ready (0-100)"""
        score = 0
        
        # Dati Master (50% totale)
        if item.price and item.price > 0: score += 10
        if item.condition_grade and item.condition_grade.strip(): score += 10
        if item.accessories_included and item.accessories_included.strip(): score += 10
        if (item.dimensions and item.dimensions.strip()) or (item.size and item.size.strip()): score += 10
        if item.fit and item.fit.strip(): score += 10
        
        # Arricchimento AI (40% totale)
        if item.seo_title and item.seo_title.strip(): score += 10
        if item.ai_description_it and item.ai_description_it.strip(): score += 15
        if item.tags and item.tags.strip(): score += 10
        if (item.material and item.material.strip()) or (item.color and item.color.strip()): score += 5
        
        # Assets (10%)
        if item.matched_images_json and item.matched_images_json != "[]": score += 10
        
        return float(min(score, 100))

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
                        self.log_api_hit("serper")
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
                        "Return ONLY valid JSON with: 'seo_title', 'tags', 'ai_description_it'.\n"
                        "IMPORTANT: Use correct Italian gender agreement for the description. "
                        f"If the product type is '{item.category or 'item'}', ensure articles and adjectives match (e.g., 'la borsa' if feminine, 'lo zaino' if masculine)."
                    )
                    ai_resp = await ai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                    ai_data = json.loads(ai_resp.choices[0].message.content)
                    result["seo_title"] = self._clean_seo_title(ai_data.get("seo_title", result["seo_title"]))
                    result["tags"] = self._clean_tags(ai_data.get("tags", result["tags"]))
                    result["ai_description_it"] = ai_data.get("ai_description_it", result["ai_description_it"])
                except Exception as e:
                    add_log(f"⚠ Errore AI Preview: {str(e)}")
        
        return result

    async def process_single_product(self, product_id: int, db, model_choice: str = "llama3"):
        """Esegue l'arricchimento mirato per un singolo prodotto utilizzando il core engine."""
        # Chiudiamo la sessione passata per evitare conflitti, dato che _enrich_single_product ne apre una nuova
        db.close()
        await self._enrich_single_product(product_id)
        
        # Recuperiamo il risultato finale per la UI
        new_db = SessionLocal()
        item = new_db.query(Product).filter(Product.id == product_id).first()
        res = {
            "seo_title": item.seo_title,
            "material": item.material,
            "dimensions": item.dimensions,
            "tags": item.tags,
            "ai_description_it": item.ai_description_it,
            "status": "ok",
            "db_status": item.status.name if item.status else "Draft"
        }
        
        # Recupero dati dalla Sandbox (raw_harvested_data) se presenti
        if item.raw_harvested_data:
            try:
                import json
                sandbox = json.loads(item.raw_harvested_data)
                # Diamo priorità ai dati della sandbox se i campi nel DB sono vuoti
                if not res["ai_description_it"]: res["ai_description_it"] = sandbox.get("ai_description_it")
                if not res["seo_title"]: res["seo_title"] = sandbox.get("seo_title")
                if not res["tags"]: 
                    s_tags = sandbox.get("tags")
                    res["tags"] = ", ".join(s_tags) if isinstance(s_tags, list) else s_tags
            except: pass
            
        new_db.close()
        return res

    def _clean_seo_title(self, title):
        """Converte il titolo in Title Case (Es: 'Gucci Marmont Media')."""
        if not title: return ""
        # Rimuove ALL CAPS se presente e converte in Title Case
        return title.strip().title()

    def _clean_tags(self, tags_list):
        """Pulisce i tag per Shopify Smart Collections: minuscolo, sola lingua italiana, blacklist."""
        if not tags_list: return []
        if isinstance(tags_list, str):
            tags_list = [t.strip() for t in tags_list.split(",") if t.strip()]
            
        blacklist = [
            "luxury", "fashion", "authentic", "glamour", "esclusivo", "prestigioso", 
            "originale", "brand", "vintage", "stile", "accessorio",
            "item", "prodotto", "collezione", "tendenza", "chic", "bag", "chain"
        ]
        
        # Translation map for common English tags
        translation_map = {
            "chain": "catena",
            "bag": "borsa",
            "clutch": "pochette",
            "shoulder": "spalla",
            "leather": "pelle",
            "gold": "oro",
            "silver": "argento",
            "black": "nero",
            "white": "bianco"
        }

        cleaned = []
        for tag in tags_list:
            # Rimuove virgolette residue e pulisce
            t = tag.lower().strip().replace("'", "").replace('"', '')
            # Traduzione forzata
            t = translation_map.get(t, t)
            
            # Rimuove punteggiatura e termini blacklist
            if t not in blacklist and len(t) > 2:
                cleaned.append(t)
        return list(set(cleaned))

    def _sanitize(self, v):
        if v is None: return ""
        s = str(v).strip().lower()
        if s in ["null", "none", "n/a", "undefined", "unknown", "nan"]:
            return ""
        return str(v).strip()

    def _sanitize_material(self, mat):
        if not mat: return ""
        m = str(mat).strip().lower()
        # Translation map for common hallucinations
        trans = {
            "cuir": "Pelle",
            "leather": "Pelle",
            "canvas": "Tela",
            "tessuto": "Tessuto",
            "gold": "Oro",
            "silver": "Argento",
            "wool": "Lana",
            "silk": "Seta",
            "cotton": "Cotone"
        }
        for eng, ita in trans.items():
            if eng in m:
                # Se è proprio la parola esatta o contenuta (es. "cuir de veau")
                return ita
        return mat.strip().capitalize()

    def run_harvester(self, ids=None):
        """Esegue l'arricchimento in batch sui prodotti in un thread dedicato."""
        db = SessionLocal()
        try:
            # Sincronizzazione stati globali
            ENGINE_STATE["status"] = "RUNNING"
            ENGINE_STATE["processed_count"] = 0
            ENGINE_STATE["current_batch_ids"] = []
            
            # Selezione Manuale vs Automatica
            if ids:
                ENGINE_STATE["pending_ids"] = list(ids)
                add_log(f"🎯 [Engine] Ricevuta selezione manuale: {len(ids)} prodotti.")
            
            to_process_ids = ENGINE_STATE["pending_ids"]
            
            if not to_process_ids:
                draft_products = db.query(Product).filter(
                    Product.status.in_([ProductStatus.Draft, ProductStatus.Error])
                ).all()
                to_process_ids = [p.id for p in draft_products]
                ENGINE_STATE["pending_ids"] = to_process_ids

            if not to_process_ids:
                add_log("🍂 [Engine] Coda vuota. Nulla da elaborare.")
                ENGINE_STATE["status"] = "FINISHED"
                PROCESS_PROGRESS["total"] = 0
                return

            PROCESS_PROGRESS["total"] = len(to_process_ids)
            PROCESS_PROGRESS["completed"] = 0
            add_log(f"🚀 [Engine] Avvio Thread dedicato. Target: {len(to_process_ids)} prodotti.")

            # Marcatura immediata
            batch_to_process = to_process_ids[:ENGINE_STATE["batch_size"]]
            db.query(Product).filter(Product.id.in_(batch_to_process)).update({"is_ai_processing": 1}, synchronize_session=False)
            db.commit()

            # Loop di elaborazione (Sync thread che lancia async)
            import asyncio
            while ENGINE_STATE["pending_ids"]:
                # Se abbiamo raggiunto il batch_size o abbiamo finito i prodotti
                if ENGINE_STATE["processed_count"] >= ENGINE_STATE["batch_size"]:
                    break

                pid = ENGINE_STATE["pending_ids"].pop(0)
                
                # Eseguiamo il task asincrono nel thread corrente
                try:
                    asyncio.run(self._enrich_single_product(pid))
                    PROCESS_PROGRESS["completed"] += 1
                except Exception as ex_coro:
                    add_log(f"⚠️ Errore nel coroutine engine per ID {pid}: {str(ex_coro)}")
                
                ENGINE_STATE["processed_count"] += 1
                ENGINE_STATE["current_batch_ids"].append(pid)
                
            # Alla fine del ciclo (sia per limite batch che per fine coda), chiediamo SEMPRE conferma
            if ENGINE_STATE["current_batch_ids"]:
                ENGINE_STATE["status"] = "WAITING_FOR_CONFIRMATION"
                add_log("⏸️ [Engine] Fase di Arricchimento completata. In attesa di Certificazione Spotlight.")
            else:
                ENGINE_STATE["status"] = "FINISHED"
                add_log("🏁 [Engine] Coda terminata. Nessun dato nuovo da certificare.")
        except Exception as e:
            add_log(f"💥 [Critical Error] Thread Engine: {str(e)}")
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
                    self.log_api_hit("serper")
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
            
            # Source Truth Extraction
            source_cat = ""
            source_tags = []
            if item.source_sheet:
                parts = [p.strip().capitalize() for p in item.source_sheet.split() if p.strip()]
                if parts:
                    source_cat = parts[0] # Scarpe
                    source_tags = parts # [Scarpe, Uomo]
            
            import ollama_bridge
            is_ollama_up = await ollama_bridge.check_ollama_status()
            
            if is_ollama_up:
                add_log(f"🤖 [AI-Agent] Analisi identità lusso per ID {item.id}...")
                local_prompt = (
                    "Analizza i dati del prodotto e restituisci SOLO un oggetto JSON valido.\n"
                    "FORMATO JSON RICHIESTO:\n"
                    "{\n"
                    "  \"seo_title\": \"Titolo in Title Case\",\n"
                    "  \"material\": \"Solo materiale fisico IN ITALIANO (es. Pelle, Tessuto, Oro)\",\n"
                    "  \"dimensions\": \"Misure\",\n"
                    "  \"product_type\": \"Categoria specifica\",\n"
                    "  \"tags\": \"Tag minuscoli, RIGOROSAMENTE IN ITALIANO (es. 'pelle', 'tracolla', 'catena'). NO parole fashion/vintage.\",\n"
                    "  \"ai_description_it\": \"Descrizione lusso ed emozionale in italiano. Usa un tono da boutique prestigiosa, soffermati sull'artigianalità e il design iconico.\"\n"
                    "}\n"
                    "RULES:\n"
                    "1. 'seo_title' must be in Title Case (Capitalize each word).\n"
                    "2. 'tags' must be short, lowercase, keywords for Shopify. TRADUCI TUTTO IN ITALIANO.\n"
                    "3. 'ai_description_it' must use STRICT Italian gender agreement (singular). "
                    f"If the product is a '{item.category}', it's likely a 'Borsa' (feminine singular) or 'Zaino' (masculine singular). "
                    "NEVER use plural like 'Le Borse' unless it's a set of items.\n"
                    "4. RESPECT EXISTING DATA: If the 'Data Prodotto' below already contains clear dimensions (e.g., '19x10x4') or material, "
                    "DO NOT change them unless they are clearly wrong or in a foreign language. Stick to the source truth as much as possible.\n"
                    "5. MANDATORY TAGS: Includi SEMPRE tra i tag il Brand, il Genere (Donna/Uomo) e il tipo di prodotto (Borsa/Scarpe/etc).\n"
                    f"INFO CATALOGO: Brand: '{item.brand}', Categoria Originale: '{item.category}'.\n"
                    f"Dati Prodotto: {combined_text}"
                )
                add_log(f"🧠 [AI-Agent] Generazione mapping selettivo (Ollama)...")
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
                        add_log(f"✨ [AI-Agent] Estrazione parametri tecnici completata (Ollama).")
                    except Exception as e:
                        add_log(f"⚠ Errore parsing AI Locale per ID {item.id}: {str(e)}")
                        add_log(f"Contenuto grezzo: {clean_json[:100]}...")
                except Exception as e:
                    add_log(f"⚠ Errore critico AI Locale: {str(e)}")

            if not used_local and self.openai_key and not self.openai_key.startswith("***"):
                add_log(f"☁ [AI-Agent] Fallback su OpenAI Cloud per ID {item.id}...")
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=self.openai_key)
                prompt = (
                    "Analyze snippets and provide a JSON with: 'seo_title', 'material', 'dimensions', 'product_type', 'tags', 'ai_description_it'.\n"
                    "RULES:\n"
                    "1. 'seo_title' MUST be in Title Case (NOT ALL CAPS).\n"
                    "2. 'material' MUST be physical material only, translated to ITALIAN (NO 'Cuir', use 'Pelle').\n"
                    "3. 'tags' must be short, lowercase, STRICTLY ITALIAN keywords (NO 'fashion', 'vintage', 'luxury', 'glamour'). TRADUCI 'chain' -> 'catena', 'bag' -> 'borsa'.\n"
                    "4. 'ai_description_it' MUST use correct Italian gender agreement (singular) and a highly professional, luxury tone. NO plural like 'Le Borse'.\n"
                    "5. SOURCE TRUTH: If the input data has specific dimensions or material, do not overwrite them with general info from your knowledge base unless requested.\n"
                    f"HINT: Original Source: '{item.source_sheet}'. Mandatory Category: {source_cat}.\n"
                    "Data:\n" + combined_text
                )
                resp = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}
                )
                resolved_data = json.loads(resp.choices[0].message.content)
                add_log(f"✨ [AI-Agent] Parametri tecnici validati via Cloud.")

            if resolved_data:
                # ISOLAMENTO SANDBOX: Salviamo in raw_harvested_data come JSON preliminare
                preliminary_results = {
                    "seo_title": self._clean_seo_title(resolved_data.get("seo_title")),
                    "material": self._sanitize_material(resolved_data.get("material")),
                    "dimensions": self._sanitize(resolved_data.get("dimensions")),
                    "product_type": item.category if item.category else self._sanitize(resolved_data.get("product_type")),
                    "ai_description_it": self._sanitize(resolved_data.get("ai_description_it")),
                    "status": "PRELIMINARY"
                }
                
                # Tag Management - Shopify Smart Rules
                raw_tags = resolved_data.get("tags", [])
                preliminary_results["tags"] = self._clean_tags(raw_tags)
                
                item.raw_harvested_data = json.dumps(preliminary_results)
                add_log(f"🧪 [Harvester] Risultati isolati in Sandbox per ID {item.id} (Shopify-Ready).")
            
            # --- PHASE 2.5: THE DEEP MAPPER (Drive Folder Matching) ---
            # STANDBY - Disabilitato temporaneamente su richiesta utente
            item.match_confidence = self.calculate_integrity(item) 
            
            # Se siamo in un batch, lo stato diventa Validating (In attesa di Spotlight)
            # Se siamo in arricchimento singolo diretto, può andare a Ready
            if ENGINE_STATE["status"] in ["RUNNING", "WAITING_FOR_CONFIRMATION"]:
                item.status = ProductStatus.Validating
            else:
                item.status = ProductStatus.Ready
                
            add_log(f"✅ [Harvester] Successo per ID {item.id} ({'Locale' if used_local else 'Cloud'})")

        except Exception as e:
            err_msg = str(e)
            item.last_ai_error = err_msg
            item.status = ProductStatus.Error
            add_log(f"❌ [Harvester Error] ID {item.id}: {err_msg}")
            item.last_ai_error = err_msg
            item.status = ProductStatus.Error
            add_log(f"❌ [Harvester Error] ID {item.id}: {err_msg}")
        
        finally:
            PROCESS_PROGRESS["completed"] += 1
            item.is_ai_processing = 0
            db.commit()
            db.close()

    async def deep_research(self, product_id: int):
        """Esegue una ricerca web approfondita per estrarre fatti tecnici e migliorare la narrazione."""
        db = SessionLocal()
        item = db.query(Product).filter(Product.id == product_id).first()
        if not item:
            db.close()
            return {"error": "Prodotto non trovato"}

        try:
            add_log(f"🧠 [Intelligence] Avvio ricerca profonda per {item.brand} {item.model}...")
            # Query più specifica per dettagli tecnici
            queries = [
                f"{item.brand} {item.model} detailed materials composition",
                f"{item.brand} {item.model} dimensions and weight",
                f"{item.brand} {item.model} authentication features guide"
            ]
            
            all_snippets = []
            async with httpx.AsyncClient(timeout=15.0) as client:
                for q in queries:
                    if self.serper_key:
                        headers = {'X-API-KEY': self.serper_key, 'Content-Type': 'application/json'}
                        resp = await client.post('https://google.serper.dev/search', headers=headers, json={"q": q, "num": 5})
                        self.log_api_hit("serper")
                        if resp.status_code == 200:
                            all_snippets.extend([o['snippet'] for o in resp.json().get('organic', [])])
            
            combined_facts = "\n".join(list(set(all_snippets)))
            add_log(f"🔎 [Intelligence] Trovati {len(all_snippets)} punti di interesse sul web.")

            # AI Prompt per Ricostruzione Autorevole
            prompt = (
                "Sei un esperto di beni di lusso. Analizza questi FATTI estratti dal web e scrivi una descrizione tecnica e lussuosa in ITALIANO.\n"
                "Includi dettagli su materiali, lavorazione e perché questo pezzo è iconico.\n"
                "Restituisci SOLO un oggetto JSON: {\"ai_description_it\": \"...\", \"material\": \"...\", \"dimensions\": \"...\"}\n"
                f"FATTI: {combined_facts}"
            )
            
            import ollama_bridge
            ai_resp = await ollama_bridge.generate_narrative("llama3", prompt)
            
            # Parsing e Salvataggio
            clean_json = ai_resp.strip()
            start_idx = clean_json.find('{')
            end_idx = clean_json.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                resolved = json.loads(clean_json[start_idx:end_idx])
                item.ai_description_it = resolved.get("ai_description_it", item.ai_description_it)
                item.material = resolved.get("material", item.material)
                item.dimensions = resolved.get("dimensions", item.dimensions)
                db.commit()
                add_log(f"✨ [Intelligence] Scheda tecnica ricostruita per {item.brand}.")
                return resolved
                
        except Exception as e:
            add_log(f"⚠️ Errore Intelligence: {str(e)}")
            return {"error": str(e)}
        finally:
            db.close()


