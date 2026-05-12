import httpx
import asyncio
from typing import Optional, List, Dict

OLLAMA_HOST = "http://127.0.0.1:11434"

async def check_ollama_status() -> bool:
    """Controlla se il demone locale Ollama è attivo e in esecuzione."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            response = await client.get(f"{OLLAMA_HOST}/api/version")
            return response.status_code == 200
        except httpx.RequestError:
            return False

async def list_local_models() -> List[Dict]:
    """Recupera la lista dei LLM installati localmente in macchina."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            if response.status_code == 200:
                data = response.json()
                return data.get("models", [])
            return []
        except httpx.RequestError:
            return []

async def install_local_model(model_name: str):
    """Innesca il pull di un modello in background (legacy)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(f"{OLLAMA_HOST}/api/pull", json={"name": model_name})
            return response.status_code == 200
        except httpx.RequestError:
            return False

import json
async def stream_install_local_model(model_name: str):
    """Esegue il pull restituendo uno stream (SSE) dei progressi."""
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream("POST", f"{OLLAMA_HOST}/api/pull", json={"name": model_name, "stream": True}) as response:
                if response.status_code != 200:
                    yield f"data: {json.dumps({'status': 'error', 'error': f'Ollama error {response.status_code}'})}\n\n"
                    return
                async for chunk in response.aiter_lines():
                    if chunk:
                        yield f"data: {chunk}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"

async def uninstall_local_model(model_name: str):
    """Elimina un modello locale."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.delete(f"{OLLAMA_HOST}/api/delete", json={"name": model_name})
            return response.status_code == 200
        except httpx.RequestError:
            return False

async def generate_narrative(model: str, prompt: str):
    """Genera testo usando un modello locale installato."""
    # Timeout aumentato a 300s per permettere ad Ollama di smaltire la coda
    # interna quando riceve 5 task in contemporanea (Hyper-Batching).
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(f"{OLLAMA_HOST}/api/generate", json={
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": False
            })
            if response.status_code == 200:
                return response.json().get("response", "")
            return "Errore: Il motore locale non ha risposto correttamente."
        except Exception as e:
            return f"Errore di connessione locale: {str(e)}"

async def analyze_image_vision(model: str, prompt: str, base64_image: str):
    """Genera testo analizzando un'immagine in base64 tramite Ollama Vision (es. moondream)."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(f"{OLLAMA_HOST}/api/generate", json={
                "model": model,
                "prompt": prompt,
                "images": [base64_image],
                "stream": False
            })
            if response.status_code == 200:
                return response.json().get("response", "")
            return f"Errore Vision AI: HTTP {response.status_code} - {response.text}"
        except Exception as e:
            return f"Errore connessione Ollama (Vision): {str(e)}"
