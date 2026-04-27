import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def test_serper():
    key = os.getenv("SERPER_API_KEY")
    print(f"🔍 Testing Serper with key: {key[:4]}...{key[-4:]}")
    
    url = "https://google.serper.dev/search"
    headers = {
        'X-API-KEY': key,
        'Content-Type': 'application/json'
    }
    payload = {"q": "Gucci Marmont Bag dimensions", "num": 1}
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            print("✅ Serper OK!")
            print(resp.json().get("organic", [])[0].get("snippet", "No snippet found"))
        else:
            print(f"❌ Serper Error: {resp.status_code}")
            print(resp.text)

if __name__ == "__main__":
    asyncio.run(test_serper())
