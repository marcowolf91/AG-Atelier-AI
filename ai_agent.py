import json
import re
import httpx
import base64
import requests
from auth_manager import get_raw_api_key

class AIAgent:
    def __init__(self):
        self.gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

    async def get_clean_json(self, prompt, model_choice="llama3"):
        """Chiama l'AI e garantisce la restituzione di un oggetto JSON pulito."""
        data = {}
        if model_choice == "gemini":
            api_key = get_raw_api_key("gemini")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt + "\nRISPONDI SOLO IN JSON."}]}]}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30.0)
                res_data = resp.json()
                print(f"DEBUG FULL RESP: {json.dumps(res_data)}")
                if 'candidates' not in res_data:
                    print(f"DEBUG AI FULL RESP ERROR: {json.dumps(res_data)}")
                    return {}
                try:
                    text_out = res_data['candidates'][0]['content']['parts'][0]['text']
                    print(f"DEBUG AI RAW RESP: {text_out[:100]}...")
                    data = self.extract_json(text_out)
                except Exception as e: 
                    print(f"DEBUG AI ERROR: {e}")
                    data = {}
        else:
            import ollama_bridge
            real_model = model_choice
            if model_choice == "llama3":
                real_model = "qwen2.5:7b"
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
        """Costruisce il prompt specifico per la moda: 'Straight to the Point' style."""
        brand = item.brand if item.brand else "N/D"
        model = item.model if item.model else "N/D"
        color = item.color if item.color else "N/D"
        material = item.material if item.material else "N/D"
        condition = item.condition_grade if item.condition_grade else "N/D"
        accessories = item.accessories_included if item.accessories_included else "N/D"
        dimensions = item.dimensions if item.dimensions else "N/D"
        size = item.size if item.size else "N/D"
        hardware = item.hardware_type if item.hardware_type else "N/D"

        # Mappatura Tag per Shopify Collections
        sheet_map = {
            "Borse Donna": "borsa, donna",
            "Pochette": "pochette, donna",
            "Borse Uomo": "borsa, uomo",
            "Borse da Viaggio": "borsa, viaggio",
            "Zaini": "zaino",
            "Piccola Pelletteria": "piccola pelletteria, uomo",
            "Piccola Pelletteria Donna": "piccola pelletteria, donna",
            "Abbigliamento Uomo": "abbigliamento, uomo",
            "Abbigliamento Donna": "abbigliamento, donna",
            "Occhiali": "occhiali",
            "Cappelli": "cappelli",
            "Scarpe Uomo": "scarpe, uomo",
            "Scarpe Donna": "scarpe, donna"
        }
        standard_tags = sheet_map.get(item.source_sheet, "")
        
        # Logica per categorie miste: chiediamo all'IA di aggiungere donna/uomo se manca
        gender_inference = ""
        if "donna" not in standard_tags and "uomo" not in standard_tags:
            gender_inference = "Analizza il prodotto e aggiungi il tag 'donna' o 'uomo' (o entrambi se unisex) in base al genere dell'articolo."

        if standard_tags:
            standard_tags += ", novita"
        
        # Istruzioni di stile ferree
        style_rules = f"""
        REGOLE DI SCRITTURA (ULTRA-MINIMAL):
        RISPONDI ESCLUSIVAMENTE IN JSON CON QUESTA STRUTTURA:
        {{
          "seo_title": "Titolo in Title Case",
          "introduzione": "Massimo 1 riga elegante",
          "punti_elenco": ["Dato 1", "Dato 2", ...]
        }}
        
        1. INTRODUZIONE: Sii naturale (es. 'Questa Celine è in ottime condizioni.').
        2. PUNTI ELENCO: Inserisci solo Condizioni, Corredo, Colore, Materiale, Hardware, Dimensioni. Ometti se N/D.
        3. TAG: {standard_tags}. {gender_inference}
        """










        full_prompt = f"""
        # TASK: Scrittore per Catalogo Luxury Second-Hand
        Genera un JSON con 'seo_title', 'tags' e 'ai_description_it' per questo prodotto.

        DATI MASTER (DA USARE RIGOROSAMENTE):
        - Titolo SEO: {item.seo_title or 'N/D'}
        - Brand: {brand}
        - Modello: {model}
        - Colore: {color}
        - Materiale: {material}
        - Condizioni: {condition}
        - Corredo/Accessori: {accessories}
        - Hardware: {hardware}
        - Dimensioni: {dimensions}
        - Misura/Taglia: {size}


        {style_rules}

        LINGUA: Italiano.
        """
        return full_prompt

