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
        """Calcola il punteggio di integrità (0-100) basato sulla qualità reale dei dati."""
        score = 0
        
        # 1. Fondamentali Master (40%)
        if item.price and item.price > 0: score += 15
        if item.condition_grade and item.condition_grade.strip(): score += 10
        if (item.dimensions and item.dimensions.strip()) or (item.size and item.size.strip()): score += 15
        
        # 2. Arricchimento AI (50%)
        if item.seo_title and len(item.seo_title.strip()) > 10: score += 10
        
        desc = (item.ai_description_it or "").strip()
        if len(desc) > 200: score += 25  # Descrizione corposa
        elif len(desc) > 50: score += 15 # Descrizione media
        elif len(desc) > 0: score += 5   # Descrizione minima
        
        if item.tags and len(item.tags.strip()) > 5: score += 10
        if (item.material and item.material.strip()) or (item.color and item.color.strip()): score += 5
        
        # 3. Media Assets (10%)
        if item.matched_images_json and item.matched_images_json != "[]": 
            score += 10
        
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

    async def get_ai_json(self, prompt, model_choice):
        """Helper per ottenere risposte JSON robuste da diversi provider AI."""
        import re
        def extract_json(text):
            if not text or not isinstance(text, str): return {}
            try:
                # Cerca il blocco { ... } più grande per estrarre il JSON puro
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    raw_json = match.group(0).strip()
                    return json.loads(raw_json)
                
                # Prova a pulire se non c'è match ma sembra JSON
                candidate = text.strip()
                if candidate.startswith('{') and candidate.endswith('}'):
                    return json.loads(candidate)
                
                return {}
            except Exception as e:
                add_log(f"⚠️ [AI-Parser] Fallimento parsing JSON da {model_choice}: {str(e)}")
                return {}

        if model_choice == "gemini":
            import auth_manager
            api_key = auth_manager.get_raw_api_key("gemini")
            # Usiamo la URL che app.py dichiara funzionante
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt + "\nRISPONDI ESCLUSIVAMENTE CON UN OGGETTO JSON. NO TESTO LIBERO."}]}]}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30.0)
                res_data = resp.json()
                if 'candidates' not in res_data:
                    print(f"❌ DEBUG GEMINI API ERROR: {res_data}")
                    add_log(f"❌ Errore Gemini API: {res_data}")
                    return {}
                text_out = res_data['candidates'][0]['content']['parts'][0]['text']
                print(f"DEBUG AI RAW (Gemini): {text_out}")
                return extract_json(text_out)
        
        elif model_choice == "openai":
            api_key = get_raw_api_key("openai")
            from openai import AsyncOpenAI
            ai_client = AsyncOpenAI(api_key=api_key)
            resp = await ai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={ "type": "json_object" }
            )
            return extract_json(resp.choices[0].message.content)
        
        else: # Llama o locale
            import ollama_bridge
            res = await ollama_bridge.generate_narrative(model_choice, prompt + "\nFORMATO RICHIESTO: JSON PURO.")
            print(f"DEBUG AI RAW (Llama): {res}")
            return extract_json(res)

    async def process_single_product(self, product_id: int, db, model_choice: str = "llama3"):
        """Esegue l'arricchimento mirato per un singolo prodotto utilizzando il core engine."""
        # Usiamo la sessione passata (db) per tutto il processo
        await self._enrich_single_product(product_id, model_choice, db_session=db)
        
        # Recuperiamo il risultato finale usando la STESSA sessione
        item = db.query(Product).filter(Product.id == product_id).first()
        if not item: return {"status": "error", "message": "Prodotto non trovato"}
        
        res = {
            "seo_title": item.seo_title,
            "material": item.material,
            "dimensions": item.dimensions,
            "tags": item.tags,
            "ai_description_it": item.ai_description_it,
            "status": "ok",
            "db_status": item.status.name if item.status else "Draft"
        }
        
        if item.raw_harvested_data:
            try:
                sandbox = json.loads(item.raw_harvested_data)
                if sandbox.get("ai_description_it"): res["ai_description_it"] = sandbox.get("ai_description_it")
                if sandbox.get("seo_title"): res["seo_title"] = sandbox.get("seo_title")
                if sandbox.get("tags"): res["tags"] = ", ".join(sandbox.get("tags")) if isinstance(sandbox.get("tags"), list) else sandbox.get("tags")
            except: pass
            
        return res

    def _clean_seo_title(self, title):
        """Pulisce il titolo SEO: rimuove 'Shop', 'Buy', converte in Italiano e Title Case."""
        if not title: return ""
        
        # Parole da rimuovere (Case Insensitive)
        blacklist = ["shop", "buy", "designer", "acquista", "vendi", "moccasins", "loafers", "for men", "for women"]
        
        t = title.lower()
        for word in blacklist:
            t = t.replace(word, "")
        
        # Pulizia spazi doppi e punteggiatura residua
        t = t.strip().replace("  ", " ").strip(",").strip("-").strip()
        
        if not t or len(t) < 5:
            return "" # Forza l'AI a riprovare o usa fallback
            
        return t.title()

    def _clean_tags(self, tags_list):
        """Pulisce i tag per Shopify Smart Collections: minuscolo, sola lingua italiana, blacklist."""
        if not tags_list: return []
        if isinstance(tags_list, str):
            tags_list = [t.strip() for t in tags_list.split(",") if t.strip()]
            
        blacklist = [
            "luxury", "fashion", "authentic", "glamour", "esclusivo", "prestigioso", 
            "originale", "brand", "vintage", "stile", "accessorio",
            "item", "prodotto", "collezione", "tendenza", "chic", "bag", "chain",
            "donna/uomo", "unisex", "misto", "vari", "unknown", "lussuoso", "elegante"
        ]
        
        # Translation map for common English tags and gender normalization
        translation_map = {
            "chain": "catena",
            "bag": "borsa",
            "clutch": "pochette",
            "shoulder": "spalla",
            "leather": "pelle",
            "gold": "oro",
            "silver": "argento",
            "black": "nero",
            "white": "bianco",
            "masc": "uomo",
            "femm": "donna",
            "male": "uomo",
            "female": "donna",
            "sneakers": "scarpe",
            "sneaker": "scarpe",
            "telera": "tela"
        }

        cleaned = []
        seen = set()
        for tag in tags_list:
            # Pulisce la stringa base (senza forzare lowercase qui)
            t = tag.strip().replace("'", "").replace('"', '')
            
            # RIMOZIONE PREFISSI (es: "brand:VALENTINO" -> "VALENTINO")
            if ":" in t:
                t = t.split(":", 1)[1].strip()
            
            # Normalizzazione per controlli
            t_low = t.lower()
            
            # Traduzione e normalizzazione genere
            if t_low in translation_map:
                t = translation_map[t_low].title()
                t_low = t.lower()
            
            # Controllo se il tag contiene parole bannate o è bannato
            is_banned = False
            for b in blacklist:
                if b == t_low or (len(t_low) > 4 and b in t_low):
                    is_banned = True
                    break
            
            if not is_banned and len(t) > 2 and t_low not in seen:
                # Forza RIGOROSAMENTE il minuscolo per tutti i tag come richiesto
                t = t_low
                
                cleaned.append(t)
                seen.add(t_low)
        return cleaned

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

    async def _enrich_single_product(self, product_id: int, model_choice: str = "llama3", db_session=None):
        # Usiamo la sessione passata o ne creiamo una nuova se siamo in standalone
        db = db_session if db_session else SessionLocal()
        should_close = db_session is None
        
        item = db.query(Product).filter(Product.id == product_id).first()
        if not item:
            if should_close: db.close()
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
            # 2. AI (Multi-Model Routing via Centralized AIAgent)
            from ai_agent import AIAgent
            agent = AIAgent()
            prompt = agent.build_fashion_prompt(item)
            
            # Arricchimento del prompt con i dati web se presenti
            if combined_text:
                prompt += f"\n\nDATI AGGIUNTIVI DAL WEB (USA SOLO PER NARRATIVA):\n{combined_text[:1000]}"
            
            add_log(f"🤖 [AI-Agent] Richiesta a {model_choice} (Master-Priority) per ID {item.id}...")
            resolved_data = await agent.get_clean_json(prompt, model_choice)

            pass

            if resolved_data:
                # ISOLAMENTO SANDBOX: Salviamo in raw_harvested_data come JSON preliminare
                preliminary_results = {
                    "seo_title": self._clean_seo_title(resolved_data.get("seo_title", "")),
                    "material": self._sanitize_material(resolved_data.get("material", "")),
                    "dimensions": self._sanitize(resolved_data.get("dimensions", "")),
                    "product_type": item.category if item.category else self._sanitize(resolved_data.get("product_type", "")),
                    "ai_description_it": self._sanitize(resolved_data.get("ai_description_it", "")),
                    "status": "PRELIMINARY"
                }
                
                # Tag Management
                raw_tags = resolved_data.get("tags", [])
                cleaned_tags = self._clean_tags(raw_tags)
                
                # Salviamo solo se abbiamo ottenuto qualcosa di sensato
                if cleaned_tags:
                    item.tags = ", ".join(cleaned_tags)
                else:
                    add_log(f"⚠️ [Harvester] L'AI non ha restituito tag validi per ID {item.id}. Mantengo quelli attuali.")
                
                # Sincronizziamo la Sandbox (Carantena Dati) - NON sovrascriviamo mai i campi certificati direttamente
                sandbox_data = json.loads(item.raw_harvested_data) if item.raw_harvested_data and item.raw_harvested_data.startswith('{') else {}
                
                # Proposte AI (da validare nello Spotlight)
                sandbox_data["proposed_seo_title"] = resolved_data.get("seo_title")
                sandbox_data["proposed_description"] = resolved_data.get("ai_description_it")
                sandbox_data["proposed_tags"] = cleaned_tags
                
                # Salvataggio tecnico per lo stato dell'engine
                sandbox_data["material"] = self._sanitize(resolved_data.get("material", item.material))
                sandbox_data["status"] = "PRELIMINARY"
                
                item.raw_harvested_data = json.dumps(sandbox_data)

                # Aggiorniamo i tag solo se il prodotto è ancora in Draft (opzionale, decidiamo noi)
                if item.status == ProductStatus.Draft and not item.tags:
                    item.tags = ", ".join(cleaned_tags)

                db.commit()
                add_log(f"🧪 [Harvester] Proposte AI salvate nella Sandbox per ID {item.id}. In attesa di revisione umana.")
                
                # Assicuriamoci che i tag siano nel risultato ritornato alla UI
                return {
                    "status": "ok",
                    "seo_title": item.seo_title,
                    "ai_description_it": item.ai_description_it,
                    "tags": item.tags,
                    "material": item.material,
                    "dimensions": item.dimensions
                }
            
            # --- PHASE 2.5: THE DEEP MAPPER (Drive Folder Matching) ---
            # STANDBY - Disabilitato temporaneamente su richiesta utente
            item.match_confidence = self.calculate_integrity(item) 
            
            # Se siamo in un batch, lo stato diventa Validating (In attesa di Spotlight)
            # Se siamo in arricchimento singolo diretto, può andare a Ready
            if ENGINE_STATE["status"] in ["RUNNING", "WAITING_FOR_CONFIRMATION"]:
                item.status = ProductStatus.Validating
            else:
                item.status = ProductStatus.Ready
                
            add_log(f"✅ [Harvester] Successo per ID {item.id}")

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
            if should_close:
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


