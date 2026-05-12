
import json
import re
import httpx
from auth_manager import get_raw_api_key

class AIAgent:
    def __init__(self):
        self.gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"

    async def get_clean_json(self, prompt, model_choice="llama3"):
        """Chiama l'AI e garantisce la restituzione di un oggetto JSON pulito."""
        data = {}
        if model_choice == "gemini":
            api_key = get_raw_api_key("gemini")
            url = f"{self.gemini_url}?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt + "\nRISPONDI SOLO IN JSON."}]}]}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30.0)
                res_data = resp.json()
                try:
                    text_out = res_data['candidates'][0]['content']['parts'][0]['text']
                    data = self.extract_json(text_out)
                except: data = {}
        else:
            import ollama_bridge
            real_model = model_choice
            if model_choice == "llama3":
                real_model = "llama3:latest"
            elif model_choice == "qwen2.5":
                real_model = "qwen2.5:7b"
                
            res = await ollama_bridge.generate_narrative(real_model, prompt)
            data = self.extract_json(res)
            
        return data

    def extract_json(self, text):
        if not text or not isinstance(text, str): return {}
        try:
            # Rimuove commenti in stile C (// e /* */) che spesso rompono il JSON
            clean_text = re.sub(r'//.*', '', text)
            clean_text = re.sub(r'/\*.*?\*/', '', clean_text, flags=re.DOTALL)
            
            match = re.search(r'\{.*\}', clean_text, re.DOTALL)
            if match:
                raw_json = match.group(0).strip()
                try:
                    return json.loads(raw_json)
                except json.JSONDecodeError:
                    # Fallback per apici singoli
                    fixed_json = raw_json.replace("'", '"')
                    try:
                        return json.loads(fixed_json)
                    except: return {}
            
            candidate = clean_text.strip()
            if candidate.startswith('{') and candidate.endswith('}'):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    fixed_json = candidate.replace("'", '"')
                    try:
                        return json.loads(fixed_json)
                    except: return {}
            
            return {}
        except: return {}

    def build_fashion_prompt(self, item):
        """Costruisce il prompt specifico per la moda con esempi reali (Few-Shot)."""
        
        # Logica Dinamica per lo Stile
        is_regen = bool(item.ai_description_it and len(item.ai_description_it) > 10)
        
        # DATI MASTER
        brand = item.brand if item.brand else "N/D"
        model = item.model if item.model else "N/D"

        if is_regen:
            # OPZIONE 3: BOUTIQUE
            style_instruction = f"""
            STRUTTURA OBBLIGATORIA: Un paragrafo intro di 2 righe (DEVE ESSERE INVENTATO E DIVERSO DALL'ESEMPIO), seguito da un elenco puntato con i dati tecnici.
            ESEMPIO DI STRUTTURA (NON COPIARE IL TESTO, INVENTANE UNO NUOVO):
            [La tua descrizione creativa e originale di 2 righe qui...]
            
            - **Materiale:** [inserire materiale]
            - **Condizioni:** [inserire condizioni fornite nei Dati Prodotto]
            - **Corredo:** [inserire corredo fornito nei Dati Prodotto]
            """
        else:
            # OPZIONE 2: IBRIDA
            style_instruction = f"""
            STRUTTURA OBBLIGATORIA: Un paragrafo descrittivo di 3 righe (DEVE ESSERE INVENTATO E 100% ORIGINALE, DIVERSO DALL'ESEMPIO), seguito RIGOROSAMENTE da un elenco con i dettagli tecnici.
            ESEMPIO DI STRUTTURA (VIETATO COPIARE IL TESTO DI ESEMPIO, SCRIVI UNA DESCRIZIONE TUA BASATA SUL PRODOTTO):
            [Inserisci qui la tua descrizione creativa, focalizzata sul modello {brand} {model}. Descrivi il design, la storia e le vere caratteristiche dell'oggetto.]
            
            Materiale: [inserire materiale]
            Dettagli: [inserire 2 dettagli estetici o funzionali specifici del modello]
            Condizioni: [inserire condizioni fornite nei Dati Prodotto]
            Corredo: [inserire corredo fornito nei Dati Prodotto]
            """

        # LOGICA GENERE / CATEGORIA
        cat_low = (item.category or "").lower()
        gender_hint = "l'articolo" # Default
        if "borsa" in cat_low or "pochette" in cat_low or "scarpe" in cat_low or "donna" in cat_low:
            gender_hint = "la borsa / la calzatura (femminile)"
        if "zaino" in cat_low or "uomo" in cat_low or "abbigliamento" in cat_low:
            gender_hint = "lo zaino / il capo (maschile)"
        if "occhiali" in cat_low:
            gender_hint = "gli occhiali (maschile plurale)"
        if "cappelli" in cat_low:
            gender_hint = "il cappello / i cappelli (maschile)"

        full_prompt = f"""
        # ISTRUZIONI PER CATALOGO DI LUSSO
        
        DATI PRODOTTO: 
        - Titolo Attuale: {item.seo_title or 'N/D'}
        - Brand: {brand}
        - Modello: {model}
        - Materiale: {item.material or 'N/D'}
        - Categoria: {item.category or 'N/D'}
        - Condizioni: {item.condition_grade or 'N/D'}
        - Corredo/Accessori: {item.accessories_included or 'N/D'}
        
        ## TASK: Genera un JSON con 'seo_title', 'tags' e 'ai_description_it'.
        
        IMPORTANTE: La descrizione DEVE seguire questo stile:
        {style_instruction}
        
        REGOLE IMPERATIVE (PENALITÀ MASSIMA SE VIOLATE):
        1. NON USARE 'SNEAKERS', usa 'Scarpe' o 'Calzature'.
        2. LINGUA: LA DESCRIZIONE DEVE ESSERE SCRITTA ESCLUSIVAMENTE IN LINGUA ITALIANA. SE SCRIVI IN INGLESE, IL SISTEMA ANDRÀ IN CRASH.
        3. Genere e Numero: USA SEMPRE IL SINGOLARE (es. "questa borsa", "questo modello", NON "queste borse") a meno che il prodotto non sia intrinsecamente plurale come gli occhiali. Assicurati di usare articoli e aggettivi corretti per {gender_hint}.
        4. Creatività e Originalità: VARIA SEMPRE IL LESSICO E LA STRUTTURA. NON usare MAI frasi fatte o cliché come "sintesi perfetta", "ideale per chi cerca", "must-have", "destinati a diventare". Sii descrittivo, elegante e unico per ogni prodotto. Focalizzati sulle caratteristiche reali e sull'heritage del brand.
        5. Formato: JSON PURO. VIETATO ASSOLUTAMENTE inserire commenti (niente // o /* */), spiegazioni o testo Markdown (niente ```json). Usa solo le doppie virgolette per chiavi e valori stringa.
        6. Titolo SEO: Il campo 'seo_title' deve contenere ESCLUSIVAMENTE il Brand e il Modello in formato Title Case (es. 'Louis Vuitton Papillon Trunk'). NON aggiungere mai categorie, aggettivi, o parole come 'Borsa', 'Di Lusso', ecc.
        """
        return full_prompt
