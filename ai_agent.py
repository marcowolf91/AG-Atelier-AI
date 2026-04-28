
import json
import re
import httpx
from auth_manager import get_raw_api_key

class AIAgent:
    def __init__(self):
        self.gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"

    async def get_clean_json(self, prompt, model_choice="llama3"):
        """Chiama l'AI e garantisce la restituzione di un oggetto JSON pulito."""
        if model_choice == "gemini":
            api_key = get_raw_api_key("gemini")
            url = f"{self.gemini_url}?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt + "\nRISPONDI SOLO IN JSON."}]}]}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30.0)
                res_data = resp.json()
                try:
                    text_out = res_data['candidates'][0]['content']['parts'][0]['text']
                    return self.extract_json(text_out)
                except: return {}
        else:
            import ollama_bridge
            res = await ollama_bridge.generate_narrative(model_choice, prompt)
            return self.extract_json(res)

    def extract_json(self, text):
        if not text: return {}
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            raw_json = match.group(0).strip() if match else text.strip()
            return json.loads(raw_json)
        except: return {}

    def build_fashion_prompt(self, item):
        """Costruisce il prompt specifico per la categorizzazione moda."""
        return f"""
        Analizza questo prodotto di lusso per Shopify.
        DATI MASTER: Brand: {item.brand}, Modello: {item.model}, Materiale: {item.material}, Categoria: {item.category}
        
        TASK: Genera un JSON con:
        1. 'tags': Una lista di stringhe pulite. 
           REGOLE CRITICHE PER I TAG:
           - SCRIVI TUTTO RIGOROSAMENTE IN MINUSCOLO.
           - NON USARE PREFISSI (NO 'brand:', NO 'categoria:', etc.).
           - I tag devono essere SOLO i valori (es: "valentino", non "VALENTINO").
           - Usa 'scarpe' al posto di 'sneakers'.
           - Usa 'tela' al posto di 'telera'.
           - Usa 'uomo' o 'donna' per il genere.
        2. 'seo_title': Brand + Modello + Materiale (in Title Case).
        3. 'ai_description_it': Descrizione elegante in italiano.
        
        FORMATO RICHIESTO:
        {{
          "seo_title": "...",
          "tags": ["valentino", "scarpe", "tela", "uomo"],
          "ai_description_it": "..."
        }}
        """
