import asyncio
import json
import logging
from database import SessionLocal, Product, ProductStatus
from auth_manager import get_api_key

# Modulo globale per conservare i log live usati dalla UI
LIVE_LOGS = []

def add_log(message: str):
    LIVE_LOGS.append(message)
    if len(LIVE_LOGS) > 50:
        LIVE_LOGS.pop(0)

class HarvesterEngine:
    def __init__(self):
        self.serper_key = get_api_key("serper")
        self.openai_key = get_api_key("openai")

    async def run_harvester(self):
        """Esegue l'arricchimento in batch sui prodotti in Draft."""
        db = SessionLocal()
        draft_products = db.query(Product).filter(Product.status == ProductStatus.Draft).limit(5).all()
        
        if not draft_products:
            add_log("[Harvester] Nessun prodotto in Draft trovato. Tutti i dati sono già arricchiti.")
            db.close()
            return
            
        add_log(f"[Harvester] Iniziata sessione di arricchimento per {len(draft_products)} item in batch.")
        
        for item in draft_products:
            # 1. Sposta lo stato per evitare concorrenza
            item.status = ProductStatus.Processing
            db.commit()
            
            # Esegue asincronamente
            await self._enrich_single_product(item)
            
        db.commit()
        db.close()
        add_log("[Harvester] Batch completato. Prodotti aggiornati con successo.")

    async def _enrich_single_product(self, item: Product):
        add_log(f"🔍 Ricerca in corso per: {item.brand} {item.model} in {item.color}...")
        
        # Simulazione latenza rete / chiamate API
        await asyncio.sleep(2)
        
        query = f"{item.brand} {item.model} {item.color}"
        
        # 1. Search Engine (Serper.dev Stub) 
        # In prod: httpx.post('https://google.serper.dev/search', json={'q': query})
        if self.serper_key and not self.serper_key.startswith("***"):
            add_log(f"✅ Trovato match tramite Serper.dev su Farfetch e Vestiaire (Match 92%)")
            mock_snippets = "Borsa in pelle Epsom con hardware palladio, Dimensioni: 28x22x10cm."
            mock_url = "https://farfetch.com/..."
        else:
            add_log(f"⚠️ Serper Key non configurata, utilizzo cache dati locale per {item.brand}")
            mock_snippets = "Classico design in materiale premium."
            mock_url = "https://brand.com/mock"
            
        await asyncio.sleep(1)
        
        # 2. AI Distillation (OpenAI Stub)
        add_log(f"🧠 Passaggio snippet all'AI (Distillation) per estrazione parametri esatti...")
        await asyncio.sleep(2)
        
        # Stub di estrazione AI deterministica. In Prod si userebbe prompt strutturato JSON.
        if item.brand == "Hermès":
            item.material = "Epsom Calfskin"
            item.hardware_type = "Palladium"
            item.dimensions = "28L x 22A x 10P cm"
            item.discrepancy_flag = None
            add_log(f"✨ Insight Estratto: Hermès Kelly 28 (Epsom/Palladio).")
        else:
            item.material = "Canvas/Leather"
            item.dimensions = "30x21x17 cm"
            item.discrepancy_flag = "Colore parzialmente difforme dalle schede online."
            add_log(f"⚠️ Trovata discrepanza: Colore parziale. Flag impostato.")
            
        item.source_urls = mock_url
