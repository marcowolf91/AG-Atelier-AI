
import os
import json
import csv
import httpx
import asyncio
from auth_manager import get_raw_api_key

async def ai_matching_test_gemini():
    api_key = get_raw_api_key("gemini")
    if not api_key:
        print("Error: Gemini API Key missing.")
        return

    # 1. Carichiamo il Catalogo dal CSV
    catalog = []
    with open('products_export.csv', mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            catalog.append({
                "id": row.get("Handle", ""),
                "title": row.get("Title", ""),
                "brand": row.get("Vendor", ""),
                "type": row.get("Type", "")
            })
    
    print(f"Catalog loaded: {len(catalog)} products.")

    # 2. Immagine da analizzare (Thumbnail di IMG_3342.jpg)
    thumbnail_url = "https://lh3.googleusercontent.com/drive-storage/AJQWtBNR5hDJpRcocAcKf9Vf6hsllDR6Q_e6BBjB0rvTIMqAV5sZDrkFAIk9iVXnHclZOlZtdcCUelb9gjsxD97HypCpOXtI1R0oMG8BPayZYZi9Eqevyw=s220"
    
    print("Analyzing image via Gemini Vision AI...")

    # Chiamata a Gemini 1.5 Flash (veloce e precisa per questo compito)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
    
    # Prompt ottimizzato per il matching
    prompt = f"""Analyze this luxury product image. 
Identify the BRAND, MODEL, and COLOR.
Then, find the BEST MATCH from the following catalog list.
If no perfect match exists, suggest the closest one.

CATALOG LIST (First 50 items):
{json.dumps(catalog[:50], indent=2)}

Return ONLY a JSON with:
"brand_detected": "...",
"model_detected": "...",
"color_detected": "...",
"match_id": "handle-of-the-product",
"confidence": 0-100
"""

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt}

            ]
        }]
    }
    
    # Aspetta, Gemini vuole l'immagine in base64. Devo scaricarla prima.
    async with httpx.AsyncClient() as client:
        img_resp = await client.get(thumbnail_url)
        img_b64 = base64.b64encode(img_resp.content).decode('utf-8')
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": "image/jpeg",
                        "data": img_b64
                    }}
                ]
            }]
        }

        response = await client.post(url, json=payload, timeout=60.0)
        result = response.json()
        
        if "candidates" in result:
            text_resp = result["candidates"][0]["content"]["parts"][0]["text"]
            print("\n--- GEMINI DEEP MATCHING RESULT ---")
            print(text_resp)
        else:
            print("Error from Gemini:", result)

import base64
if __name__ == "__main__":
    asyncio.run(ai_matching_test_gemini())
