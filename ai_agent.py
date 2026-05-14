import json
import re
import httpx
import base64
import requests
from auth_manager import get_raw_api_key

class AIAgent:
    def __init__(self):
        self.gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

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

    def analyze_image_vision(self, file_id, prompt):
        """Analizza un'immagine usando Gemini 1.5 Flash."""
        api_key = get_raw_api_key("gemini")
        url = f"{self.gemini_url}?key={api_key}"
        
        try:
            from google_auth import get_credentials
            from googleapiclient.discovery import build
            creds = get_credentials()
            drive_service = build('drive', 'v3', credentials=creds)
            
            content = drive_service.files().get_media(fileId=file_id).execute()
            b64_image = base64.b64encode(content).decode('utf-8')
            
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_image
                            }
                        }
                    ]
                }]
            }
            
            resp = requests.post(url, json=payload, timeout=30.0)
            res_data = resp.json()
            
            return res_data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            return f"Error: {str(e)}"

    def extract_json(self, text):
        if not text or not isinstance(text, str): return {}
        try:
            clean_text = re.sub(r'//.*', '', text)
            clean_text = re.sub(r'/\*.*?\*/', '', clean_text, flags=re.DOTALL)
            
            match = re.search(r'\{.*\}', clean_text, re.DOTALL)
            if match:
                raw_json = match.group(0).strip()
                try:
                    return json.loads(raw_json)
                except json.JSONDecodeError:
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
        is_regen = bool(item.ai_description_it and len(item.ai_description_it) > 10)
        brand = item.brand if item.brand else "N/D"
        model = item.model if item.model else "N/D"

        if is_regen:
            style_instruction = f"""
            STRUTTURA OBBLIGATORIA: Un paragrafo intro di 2 righe, seguito da un elenco puntato con i dati tecnici.
            """
        else:
            style_instruction = f"""
            STRUTTURA OBBLIGATORIA: Un paragrafo descrittivo di 3 righe, seguito RIGOROSAMENTE da un elenco con i dettagli tecnici.
            """

        cat_low = (item.category or "").lower()
        gender_hint = "l'articolo"
        if "borsa" in cat_low or "pochette" in cat_low or "scarpe" in cat_low or "donna" in cat_low:
            gender_hint = "la borsa / la calzatura (femminile)"
        
        full_prompt = f"""
        # ISTRUZIONI PER CATALOGO DI LUSSO
        DATI PRODOTTO: 
        - Titolo Attuale: {item.seo_title or 'N/D'}
        - Brand: {brand}
        - Modello: {model}
        - Materiale: {item.material or 'N/D'}
        
        ## TASK: Genera un JSON con 'seo_title', 'tags' e 'ai_description_it'.
        {style_instruction}
        """
        return full_prompt
