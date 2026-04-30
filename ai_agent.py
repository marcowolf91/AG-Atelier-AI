
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
            real_model = "llama3:latest" if model_choice == "llama3" else model_choice
            res = await ollama_bridge.generate_narrative(real_model, prompt)
            data = self.extract_json(res)
            
        return data

    def extract_json(self, text):
        if not text or not isinstance(text, str): return {}
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                raw_json = match.group(0).strip()
                return json.loads(raw_json)
            
            candidate = text.strip()
            if candidate.startswith('{') and candidate.endswith('}'):
                return json.loads(candidate)
            
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
            STRUTTURA: Un paragrafo corto di massimo 2 righe + elenco puntato breve.
            ESEMPIO:
            Un'icona di stile senza tempo. L'eleganza firmata Valentino elevata alla massima potenza.
            
            - **Stato:** Ottimo
            - **Corredo:** Scatola originale inclusa
            """
        else:
            # OPZIONE 2: IBRIDA
            style_instruction = f"""
            STRUTTURA: Un paragrafo emozionale di 3 righe + dati tecnici chiari.
            ESEMPIO:
            Queste scarpe Valentino in tela rappresentano la sintesi perfetta tra artigianalità e stile moderno. Ideali per chi cerca un pezzo iconico ma versatile, capace di completare ogni outfit con l'eleganza distintiva del brand.
            
            Materiale: Tela di alta qualità
            Dettagli: Design minimalista, suola confortevole
            Condizioni: Ottime, pronte all'uso
            Corredo: Scatola originale inclusa
            """

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
        
        REGOLE:
        1. NON USARE 'SNEAKERS', usa 'Scarpe' o 'Calzature'.
        2. Lingua: ITALIANO.
        3. Formato: JSON PURO.
        """
        return full_prompt
