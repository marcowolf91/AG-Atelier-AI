from fastapi import FastAPI, Depends, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json
import os
import io
import httpx
import datetime

# Database & Core
from database import engine, SessionLocal, Product, ProductStatus, get_db, Setting
import auth_manager
import google_auth

app = FastAPI(title="Atelier AI")

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

def get_system_status():
    """Ritorna lo stato di tutte le chiavi/servizi."""
    status = {
        "google": auth_manager.check_google_auth(),
        "openai": auth_manager.get_api_key("openai") is not None,
        "anthropic": auth_manager.get_api_key("anthropic") is not None,
        "gemini": auth_manager.get_api_key("gemini") is not None,
        "serper": auth_manager.get_api_key("serper") is not None,
        "shopify": auth_manager.get_api_key("shopify_token") is not None and auth_manager.get_api_key("shopify_url") is not None,
        
        # Valori mascherati
        "openai_val": auth_manager.get_api_key("openai"),
        "anthropic_val": auth_manager.get_api_key("anthropic"),
        "gemini_val": auth_manager.get_api_key("gemini"),
        "serper_val": auth_manager.get_api_key("serper"),
        "shopify_token_val": auth_manager.get_api_key("shopify_token"),
        "shopify_url_val": auth_manager.get_api_key("shopify_url")
    }
    # Verifica se quasi tutto è configurato per il led verde principale (mock logica base)
    all_go = status["google"] and status["serper"] and status["shopify"]
    return status, all_go

@app.get("/")
def read_dashboard(request: Request, db: Session = Depends(get_db)):
    # Parametri Filtro
    q = request.query_params.get("q", "").strip()
    brand_filter = request.query_params.get("brand", "")
    status_filter = request.query_params.get("status", "")

    query = db.query(Product)
    
    # Filtri
    if q:
        query = query.filter(Product.model.ilike(f"%{q}%") | Product.brand.ilike(f"%{q}%"))
    if brand_filter:
        query = query.filter(Product.brand == brand_filter)
    if status_filter:
        query = query.filter(Product.status == status_filter)

    total_products = db.query(Product).count()
    validating = db.query(Product).filter(Product.status == ProductStatus.Validating).count()
    published = db.query(Product).filter(Product.status == ProductStatus.Published).count()
    errors = db.query(Product).filter(Product.status == ProductStatus.Error).count()
    drafts = db.query(Product).filter(Product.status == ProductStatus.Draft).count()
    
    serp_used = total_products - drafts
    from sqlalchemy.sql import func
    total_val_calc = db.query(func.sum(Product.price)).scalar() or 0.0

    status_sys, all_go = get_system_status()

    # Brand con conteggi per filtro
    brand_counts_raw = db.query(Product.brand, func.count(Product.id)).group_by(Product.brand).all()
    brands_with_counts = []
    for b_name, b_count in brand_counts_raw:
        if b_name:
            brands_with_counts.append({"name": b_name, "count": b_count})
    brands_with_counts = sorted(brands_with_counts, key=lambda x: x["name"])

    context = {
        "request": request,
        "kpi_total": total_products,
        "kpi_validating": validating,
        "kpi_published": published,
        "kpi_errors": errors,
        "kpi_serp_used": serp_used,
        "kpi_total_value": round(total_val_calc, 0),
        "active_page": "dashboard",
        "auth_status": status_sys,
        "all_systems_go": all_go,
        "recent_products": query.order_by(Product.id.desc()).limit(100).all(),
        "brands_list": brands_with_counts,
        "filter_q": q,
        "filter_brand": brand_filter,
        "filter_status": status_filter
    }

    return templates.TemplateResponse(
        request=request, name="dashboard.html", context=context
    )

@app.get("/the-loom")
def the_loom(request: Request, db: Session = Depends(get_db)):
    status_sys, all_go = get_system_status()
    
    # Parametri di Filtro
    q = request.query_params.get("q", "").strip()
    brand_filter = request.query_params.get("brand", "")
    status_filter = request.query_params.get("status", "")
    
    # Visualizziamo tutto ciò che è in entrata (Inbound), escludendo solo i già pubblicati o pronti
    query = db.query(Product).filter(Product.status.in_([ProductStatus.Draft, ProductStatus.Error, ProductStatus.Processing, ProductStatus.Validating]))
    
    if q:
        query = query.filter(Product.model.ilike(f"%{q}%") | Product.brand.ilike(f"%{q}%"))
    
    if brand_filter:
        query = query.filter(Product.brand == brand_filter)
    
    if status_filter:
        filters = status_filter.split(',')
        from sqlalchemy import or_
        or_conditions = []
        
        for f in filters:
            if f == "no_brand":
                or_conditions.append((Product.brand == None) | (Product.brand == ""))
            elif f == "no_price":
                or_conditions.append((Product.price == None) | (Product.price == 0))
            elif f == "no_material":
                or_conditions.append((Product.material == None) | (Product.material == ""))
            elif f == "no_color":
                or_conditions.append((Product.color == None) | (Product.color == ""))
            elif f == "no_dims":
                or_conditions.append((Product.dimensions == None) | (Product.dimensions == ""))
            elif f == "no_images":
                or_conditions.append((Product.matched_images_json == None) | (Product.matched_images_json == "[]") | (Product.matched_images_json == ""))
            elif f == "Error":
                query = query.filter(Product.status == ProductStatus.Error)
            elif f == "Draft":
                query = query.filter(Product.status == ProductStatus.Draft)
            else:
                query = query.filter(Product.status == f)
        
        if or_conditions:
            query = query.filter(or_( *or_conditions ))
        
    products = query.order_by(Product.id.desc()).limit(1000).all()
    
    # Lista Brand Unici per il filtro
    brands = [r[0] for r in db.query(Product.brand).distinct().all() if r[0]]
    
    sheet_info = {}
    try:
        import os, json
        if os.path.exists("workspace_config.json"):
            with open("workspace_config.json", 'r') as f:
                cnf = json.load(f)
                sid = cnf.get("sheet_id")
                if sid:
                    sheet_info = get_sheets_stats(sid)
    except:
        pass

    context = {
        "request": request, 
        "active_page": "the_loom", 
        "auth_status": status_sys, 
        "all_systems_go": all_go, 
        "products": products,
        "brands": sorted(brands),
        "sheet_info": sheet_info,
        "filter_q": q,
        "filter_brand": brand_filter,
        "filter_status": status_filter
    }
    return templates.TemplateResponse(
        request=request, name="the_loom.html", context=context
    )

@app.post("/api/database/purge")
def purge_database(db: Session = Depends(get_db)):
    """Elimina tutti i prodotti per ricaricare da zero dal foglio."""
    db.query(Product).delete()
    db.commit()
    return {"status": "ok"}

@app.delete("/api/product/{pid}")
def delete_product(pid: int, db: Session = Depends(get_db)):
    """Rimuove un singolo prodotto dal database locale."""
    item = db.query(Product).filter(Product.id == pid).first()
    if item:
        db.delete(item)
        db.commit()
    return {"status": "ok"}


@app.get("/api/logs")
def get_logs():
    """Restituisce gli ultimi log dalla Sala Macchine per la console UI."""
    from harvester import LIVE_LOGS
    return {"logs": LIVE_LOGS}

@app.get("/the-harvester")
def the_harvester(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    # Mostriamo solo prodotti da arricchire o in errore per focalizzare l'utente
    products = db.query(Product).filter(
        (Product.seo_title == None) | (Product.status == ProductStatus.Error) | (Product.is_ai_processing == 1)
    ).order_by(Product.id.desc()).all()
    context = {"request": request, "active_page": "the_harvester", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse(request=request, name="the_harvester.html", context=context)

@app.post("/api/harvester/enrich_single/{pid}")
async def enrich_single(pid: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Lancia l'arricchimento AI su un singolo prodotto specifico."""
    item = db.query(Product).filter(Product.id == pid).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    item.is_ai_processing = 1
    item.last_ai_error = None
    db.commit()
    
    from harvester import HarvesterEngine
    engine = HarvesterEngine()
    background_tasks.add_task(engine._enrich_single_product, pid)
    return {"status": "ok"}

@app.get("/asset-vault")
def asset_vault(request: Request, db: Session = Depends(get_db)):
    status_sys, all_go = get_system_status()
    st_filter = request.query_params.get("status", "")
    
    query = db.query(Product)
    if st_filter == "ready":
        query = query.filter(Product.status == ProductStatus.Ready)
    elif st_filter == "published":
        query = query.filter(Product.status == ProductStatus.Published)
        
    products = query.order_by(Product.id.desc()).all()
    context = {
        "request": request, 
        "active_page": "asset_vault", 
        "auth_status": status_sys, 
        "all_systems_go": all_go, 
        "products": products,
        "filter_status": st_filter
    }
    return templates.TemplateResponse(request=request, name="asset_vault.html", context=context)
    
@app.get("/shopify-mirror")
async def shopify_mirror(request: Request, db: Session = Depends(get_db)):
    status_sys, all_go = get_system_status()
    context = {
        "request": request, 
        "active_page": "shopify_mirror", 
        "auth_status": status_sys, 
        "all_systems_go": all_go
    }
    return templates.TemplateResponse(request=request, name="shopify_mirror.html", context=context)

@app.get("/api/shopify/catalog")
async def get_shopify_catalog():
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    return await bridge.fetch_shopify_catalog()

@app.post("/api/product/update/{product_id}")
async def update_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product: return {"error": "Prodotto non trovato"}
    
    # Aggiornamento campi base
    if "brand" in data: product.brand = data["brand"]
    if "model" in data: product.model = data["model"]
    if "price" in data: product.price = float(data["price"])
    
    if "sku" in data and data["sku"] != product.sku:
        # Verifica unicità SKU
        exists = db.query(Product).filter(Product.sku == data["sku"]).first()
        if exists: return {"error": "SKU già esistente"}
        product.sku = data["sku"]
        
    db.commit()
    return {"status": "ok"}

@app.post("/api/shopify/import")
async def import_shopify_product(request: Request):
    data = await request.json()
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    return await bridge.import_product_to_pim(data)

@app.post("/api/vault/manual-match")
async def vault_manual_match(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    pid = data.get("product_id")
    fid = data.get("folder_id")
    
    product = db.query(Product).filter(Product.id == pid).first()
    if not product: return {"error": "Prodotto non trovato"}
    
    # Aggiorniamo l'ancoraggio della cartella
    product.drive_folder_id = fid
    # Resettiamo i match per forzare la ricarica dei file dalla nuova cartella
    product.matched_images_json = "[]"
    product.image_match_score = 0.0
    
    db.commit()
    
    # Inneschiamo opzionalmente una scansione dei file interni (mock per ora, o richiama sync_drive_images)
    return {"status": "ok", "message": "Cartella collegata con successo."}

import base64
@app.post("/api/vault/seo-rename/{product_id}")
async def vault_seo_rename(product_id: int, db: Session = Depends(get_db)):
    """Rinomina Deep: Cartella + Immagini su Drive in base al Titolo SEO."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product or not product.seo_title:
        return {"status": "error", "message": "Titolo SEO mancante o approvazione assente."}
        
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        
        base_name = product.seo_title.lower().replace(" ", "-").replace("/", "-")
        # Pulizia caratteri speciali residui
        import re
        base_name = re.sub(r'[^a-z0-9\-]', '', base_name)
        
        renamed_count = 0
        
        # 1. Rinomina Cartella Principale
        if product.drive_folder_id:
            service.files().update(
                fileId=product.drive_folder_id,
                body={"name": base_name.upper()}, # Cartella in MAIUSCOLO per ordine
                supportsAllDrives=True
            ).execute()
            renamed_count += 1

        # 2. Rinomina File Interni / Associati
        img_meta = json.loads(product.matched_images_json) if product.matched_images_json else []
        # Supporto sia per file singoli che file in cartella
        files_to_process = [img for img in img_meta if img.get("type") == "image"]
        
        for idx, img in enumerate(files_to_process, start=1):
            f_id = img.get("id")
            if not f_id: continue
            
            # Estraiamo estensione originale
            ext = ".jpg"
            if "." in img.get("name", ""):
                ext = "." + img.get("name").split(".")[-1]
                
            new_filename = f"{base_name}-{idx}{ext}"
            
            service.files().update(
                fileId=f_id,
                body={"name": new_filename},
                supportsAllDrives=True
            ).execute()
            renamed_count += 1
            
        # 3. Aggiorniamo il JSON locale per riflettere i nuovi nomi (opzionale ma consigliato)
        # Per semplicità qui potremmo risincronizzare il mapper, ma facciamo un commit rapido
        db.commit()
        
        return {"status": "ok", "message": f"Deep Rename completato: {renamed_count} elementi rinominati su Drive."}
        
    except Exception as e:
        return {"error": str(e)}

@app.get("/the-muse")
def the_muse(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    # Mostriamo prodotti pronti per la scrittura narrativa (quelli arricchiti)
    products_raw = db.query(Product).filter(Product.seo_title != None).all()
    
    products = []
    for p in products_raw:
        products.append({
            "id": p.id,
            "brand": p.brand,
            "model": p.model,
            "seo_title": p.seo_title,
            "material": p.material,
            "color": p.color,
            "hardware_type": p.hardware_type,
            "condition_grade": p.condition_grade,
            "price": p.price
        })

    context = {
        "request": request,
        "active_page": "the_muse",
        "auth_status": status,
        "all_systems_go": all_go,
        "products": products
    }
    return templates.TemplateResponse(request=request, name="the_muse.html", context=context)

@app.get("/shopify-cloud")
def shopify_cloud(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "shopify_cloud", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse(request=request, name="shopify_cloud.html", context=context)

@app.get("/ai-foundry")
def ai_foundry(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "ai_foundry", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse(request=request, name="ai_foundry.html", context=context)

@app.get("/catalog")
def open_catalog(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    products = db.query(Product).filter(Product.status.in_([ProductStatus.Published, ProductStatus.Ready])).all()
    context = {"request": request, "active_page": "catalog", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse(request=request, name="catalog.html", context=context)

@app.post("/api/catalog/sync/{product_id}")
async def catalog_sync_single(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    item = db.query(Product).filter(Product.id == product_id).first()
    if item:
        if "price" in data:
            try: item.price = float(data["price"])
            except ValueError: pass
        db.commit()
        # Mock productSet live logic
    return {"status": "ok"}

@app.post("/api/sync")
async def trigger_sync(request: Request, db: Session = Depends(get_db)):
    """Avvia la sync dal Google Sheet configurato alla base dati SQLite locale."""
    from sync_engine import engine
    import google_auth
    data = await request.json()
    range_name = data.get("range", "A:Z")
    
    config = get_settings()
    sid = config.get("sheet_id")
    
    if not sid:
        return {"status": "error", "message": "Sheet ID non configurato"}

    # Se il range è "A:Z" (globale), sincronizziamo TUTTI i fogli disponibili nel file
    if range_name == "A:Z":
        try:
            creds = google_auth.get_credentials()
            from googleapiclient.discovery import build
            service = build('sheets', 'v4', credentials=creds)
            
            # Analisi Struttura Spreadsheet
            spreadsheet = service.spreadsheets().get(spreadsheetId=sid).execute()
            sheets = spreadsheet.get('sheets', [])
            
            total_imported = 0
            total_updated = 0
            
            # Ciclo Sequenziale (Mano a Mano)
            for s in sheets:
                s_title = s['properties']['title']
                # Usiamo il nome esatto del foglio come range
                res = await engine.sync_sheets(sid, db, range_name=f"'{s_title}'!A:Z")
                total_imported += res.get("imported", 0)
                total_updated += res.get("updated", 0)
            
            return {
                "status": "ok", 
                "imported": total_imported, 
                "updated": total_updated,
                "sheets_processed": len(sheets)
            }
            
        except Exception as e:
            print(f"OMNIA SYNC ERROR: {e}")
            return {"status": "error", "message": str(e)}
    
    # Sync Singolo (specifico per un tab)
    res = await engine.sync_sheets(sid, db, range_name=range_name)
    return {
        "status": "ok", 
        "imported": res.get("imported", 0),
        "updated": res.get("updated", 0)
    }

@app.post("/api/harvester/start")
async def start_harvester(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    ids = data.get("ids", [])
    import harvester
    engine = harvester.HarvesterEngine()
    background_tasks.add_task(engine.run_harvester, ids)
    return {"status": "started"}

@app.post("/api/harvester/preview")
async def harvester_preview(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    pid = data.get("product_id")
    action = data.get("action", "all")
    
    item = db.query(Product).filter(Product.id == pid).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    import harvester
    engine = harvester.HarvesterEngine()
    preview = await engine.get_preview(item, action)
    return preview

@app.post("/api/harvester/confirm")
async def harvester_confirm(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    pid = data.get("product_id")
    
    item = db.query(Product).filter(Product.id == pid).first()
    if item:
        if "seo_title" in data: item.seo_title = data["seo_title"]
        if "ai_description_it" in data: item.ai_description_it = data["ai_description_it"]
        if "material" in data: item.material = data["material"]
        if "dimensions" in data: item.dimensions = data["dimensions"]
        if "tags" in data: item.tags = data["tags"]
        if "images" in data and isinstance(data["images"], list): 
            item.matched_images_json = json.dumps(data["images"])
            item.image_match_score = float(len(data["images"]))
        
        # Dopo la conferma manuale, il prodotto è PRONTO
        item.status = ProductStatus.Ready
        db.commit()
    return {"status": "ok"}

@app.get("/api/harvester/logs")
def get_harvester_logs():
    import harvester
    return {"logs": harvester.LIVE_LOGS}

@app.get("/engine-room")
def engine_room(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    google_connected = google_auth.is_google_connected()
    
    error = request.query_params.get("error")
    success = request.query_params.get("success")

    context = {
        "request": request,
        "active_page": "engine_room",
        "auth_status": status,
        "all_systems_go": all_go,
        "google_connected": google_connected,
        "error_msg": error,
        "success_msg": success
    }
    return templates.TemplateResponse(request=request, name="engine_room.html", context=context)

@app.get("/auth/google/login")
def google_login(request: Request):
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/auth/google/callback"
    url, err = google_auth.get_google_auth_url(redirect_uri)
    if not url:
        return RedirectResponse(url="/engine-room?error=secrets_missing", status_code=303)
    return RedirectResponse(url=url)

@app.get("/api/drive/list")
def list_drive_items(parent_id: str = "root", item_type: str = "folder"):
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        
        if parent_id == 'sharedWithMe':
            # Visualizza cartelle radice condivise
            results = service.files().list(
                q="sharedWithMe=true and trashed=false",
                fields="files(id, name, mimeType, owners(displayName, emailAddress))",
                pageSize=50,
                orderBy="folder, name",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute()
        else:
            results = service.files().list(
                q=f"'{parent_id}' in parents and trashed=false",
                fields="files(id, name, mimeType, owners(displayName, emailAddress))",
                pageSize=50,
                orderBy="folder, name",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute()
        return {"files": results.get("files", []), "parent_id": parent_id}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/drive/create-folder")
async def create_drive_folder(request: Request):
    data = await request.json()
    parent_id = data.get("parent_id", "root")
    folder_name = data.get("name", "Nuova Cartella")
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id and parent_id != 'sharedWithMe':
            file_metadata['parents'] = [parent_id]
            
        folder = service.files().create(
            body=file_metadata, 
            fields='id, name', 
            supportsAllDrives=True
        ).execute()
        return folder
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/drive/clone-structure")
async def clone_drive_structure(request: Request):
    data = await request.json()
    source_id = data.get("source_id")
    target_id = data.get("target_id")
    
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    if not source_id or not target_id: return {"error": "Parametri mancanti"}
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        
        folders_created = 0
        def clone_recursive(src, dst):
            nonlocal folders_created
            # Get child folders of src
            res = service.files().list(
                q=f"'{src}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)",
                pageSize=100,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute()
            
            children = res.get("files", [])
            for child in children:
                # Modest rate-limit safeguard
                if folders_created > 100: break
                
                # Check if folder already exists in dst
                existing_res = service.files().list(
                    q=f"'{dst}' in parents and name='{child['name']}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    fields="files(id, name)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute()
                
                existing = existing_res.get("files", [])
                
                if not existing:
                    # Create corresponding folder in dst
                    file_metadata = {
                        'name': child['name'],
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [dst]
                    }
                    new_folder = service.files().create(
                        body=file_metadata, fields='id', supportsAllDrives=True
                    ).execute()
                    folders_created += 1
                    target_child_id = new_folder['id']
                else:
                    target_child_id = existing[0]['id']
                
                # Recurse
                clone_recursive(child['id'], target_child_id)

        clone_recursive(source_id, target_id)
        return {"status": "success", "folders_created": folders_created}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/drive/sync")
async def sync_drive_images(db: Session = Depends(get_db)):
    """Matching Intelligente ad Alta Efficienza (SmartMapper)."""
    import google_auth
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        config = get_settings()
        root_folder_id = config.get("drive_images_root_id")
        
        if not root_folder_id:
            return {"error": "Missing drive_images_root_id in settings"}
            
        add_log(f"📡 [SmartMapper] Indicizzazione Drive in corso (Folder: {root_folder_id})...")
        
        # 1. Scansione Massiva Unica (Recursiva 1-level + root)
        drive_inventory = []
        page_token = None
        
        # Recuperiamo cartelle e immagini
        while True:
            q = f"'{root_folder_id}' in parents and trashed=false"
            res = service.files().list(
                q=q, 
                fields="nextPageToken, files(id, name, mimeType, thumbnailLink, webViewLink)",
                pageSize=1000, 
                pageToken=page_token,
                supportsAllDrives=True, 
                includeItemsFromAllDrives=True
            ).execute()
            
            drive_inventory.extend(res.get('files', []))
            page_token = res.get('nextPageToken')
            if not page_token: break

        # 2. Matching Granulare in Memoria
        products = db.query(Product).all()
        matched_count = 0
        
        for p in products:
            # Creiamo un set di parole chiave dal prodotto
            keywords = set()
            if p.brand: keywords.add(p.brand.lower())
            if p.model: keywords.update(p.model.lower().replace('-', ' ').split())
            if p.sku: keywords.add(p.sku.lower())
            if p.seo_title: keywords.update(p.seo_title.lower().replace('-', ' ').split()[:4])
            
            # Filtro qualità parole
            keywords = {k for k in keywords if len(k) > 2}
            if not keywords: continue
            
            valid_images = []
            sku_lower = p.sku.lower() if p.sku else None
            
            for item in drive_inventory:
                name_norm = item['name'].lower().replace('_', ' ').replace('-', ' ')
                
                # Check Intersezione
                match_count = sum(1 for k in keywords if k in name_norm)
                
                # Regola: SKU match (Priorità Massima) o Euristica Brand/Keywords
                is_sku_match = sku_lower and (sku_lower in name_norm)
                if is_sku_match or (p.brand and p.brand.lower() in name_norm and match_count >= 2) or (match_count >= 3):
                    valid_images.append({
                        "id": item["id"],
                        "name": item["name"],
                        "thumb": item.get("thumbnailLink"),
                        "link": item.get("webViewLink"),
                        "type": "folder" if "folder" in item["mimeType"] else "image"
                    })
            
            if valid_images:
                # Se tra i match c'è una cartella, la impostiamo come folder principale
                primary_folder = next((img for img in valid_images if img.get("type") == "folder"), None)
                if primary_folder:
                    p.drive_folder_id = primary_folder["id"]
                    p.drive_folder_url = primary_folder.get("link")
                
                p.matched_images_json = json.dumps(valid_images)
                p.image_match_score = float(len(valid_images))
                matched_count += 1
            else:
                p.matched_images_json = "[]"
                p.image_match_score = 0.0
                p.drive_folder_id = None
                
        db.commit()
        add_log(f"✅ [SmartMapper] Sincronizzazione finita: {matched_count} prodotti mappati.")
        return {"status": "ok", "matched": matched_count}
        
    except Exception as e:
        add_log(f"❌ [SmartMapper Error] {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/api/drive/stats")
def get_drive_stats(folder_id: str):
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        
        # Get Folder Name
        try:
            folder_meta = service.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
            folder_name = folder_meta.get("name", "Sconosciuto")
        except:
            folder_name = "Errore Lettura"
        
        # Statistiche ricorsive
        unique_image_ids = set()
        total_folders = 0
        MAX_FOLDERS = 100 
        folders_to_scan = [folder_id]
        
        while folders_to_scan and total_folders < MAX_FOLDERS:
            current_folder = folders_to_scan.pop(0)
            page_token = None
            
            while True:
                res = service.files().list(
                    q=f"'{current_folder}' in parents and trashed=false",
                    fields="nextPageToken, files(id, mimeType)",
                    pageSize=1000,
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute()
                
                items = res.get("files", [])
                for f in items:
                    f_id = f.get('id')
                    mime = f.get('mimeType', '')
                    
                    if mime.startswith('image/'):
                        unique_image_ids.add(f_id)
                    elif mime == 'application/vnd.google-apps.folder':
                        total_folders += 1
                        if total_folders < MAX_FOLDERS:
                            folders_to_scan.append(f_id)
                
                page_token = res.get('nextPageToken')
                if not page_token:
                    break
                    
        total_images = len(unique_image_ids)
                    
        # Conteggio Reale Prodotti Già Associati nel DB
        db = SessionLocal()
        try:
            processed = db.query(Product).filter(Product.matched_images_json != None).count()
        finally:
            db.close()
        
        return {
            "folder_name": folder_name,
            "image_count": total_images,
            "folder_count": total_folders,
            "processed_count": processed,
            "warning": "Limite cartelle raggiunto" if total_folders >= MAX_FOLDERS else None
        }
    except Exception as e:
        return {"error": str(e)}

# --- MAPPING SETTINGS ---
@app.get("/api/settings/mapping")
def get_mapping(context: str = "global"):
    import os
    if os.path.exists("mapping_config.json"):
        with open("mapping_config.json", 'r') as f:
            full_map = json.load(f)
            return full_map.get(context, full_map.get("global", {}))
    return {}

@app.post("/api/settings/mapping")
async def save_mapping(request: Request, context: str = "global"):
    data = await request.json()
    import os
    full_map = {}
    if os.path.exists("mapping_config.json"):
        with open("mapping_config.json", 'r') as f:
            full_map = json.load(f)
    
    full_map[context] = data
    with open("mapping_config.json", 'w') as f:
        json.dump(full_map, f)
    return {"status": "success"}

@app.get("/api/settings/mapping/contexts")
def get_mapping_contexts(db: Session = Depends(get_db)):
    # Restituisce i nomi dei fogli presenti nel DB + 'global'
    from sqlalchemy import distinct
    sheets = db.query(distinct(Product.source_sheet)).all()
    contexts = ["global"]
    for s in sheets:
        if s[0]: contexts.append(s[0])
    return contexts

# --- FOLDER BROWSER ---
@app.get("/api/files/browse")
def browse_files(path: str = "/"):
    import os
    try:
        if not path or path == "": path = os.path.expanduser("~")
        
        items = []
        # Aggiungiamo il "parent"
        parent = os.path.dirname(path)
        items.append({"name": "..", "path": parent, "type": "dir"})
        
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_dir() and not entry.name.startswith('.'):
                    items.append({
                        "name": entry.name,
                        "path": entry.path,
                        "type": "dir"
                    })
        return {"current_path": path, "items": items}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/settings/tasks")
def get_task_assignments():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cnf = json.load(f)
            return cnf.get("task_assignments", {
                "description": "llama3",
                "vision": "moondream",
                "naming": "mistral"
            })
    return {}

@app.post("/api/settings/tasks")
async def save_task_assignments(request: Request):
    data = await request.json()
    cnf = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cnf = json.load(f)
    
    cnf["task_assignments"] = data
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cnf, f)
    return {"status": "success"}

import json

CONFIG_FILE = "workspace_config.json"

@app.get("/api/settings")
def get_settings():
    import os
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

@app.post("/api/settings")
async def save_settings(request: Request):
    data = await request.json()
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f)
    return {"status": "success"}

@app.get("/api/sheets/stats")
def get_sheets_stats(sheet_id: str):
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    try:
        from googleapiclient.discovery import build
        service = build('sheets', 'v4', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Get Sheet Name
        try:
            file_meta = drive_service.files().get(fileId=sheet_id, fields="name", supportsAllDrives=True).execute()
            sheet_name = file_meta.get("name", "Foglio Senza Nome")
        except:
            sheet_name = "Foglio"
        
        # Get sub-sheets
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets_info = spreadsheet.get('sheets', [])
        sheets_list = [s['properties']['title'] for s in sheets_info]
        
        # --- STATISTICHE DAL DATABASE (REALTÀ PIM) ---
        db = SessionLocal()
        try:
            total_db = db.query(Product).count()
            # Prodotti che hanno già titolo SEO e descrizione italiana generata
            ready_db = db.query(Product).filter(Product.seo_title != None, Product.ai_description_it != None).count()
            # Prodotti orfani di titoli o tag
            missing_title = db.query(Product).filter(Product.seo_title == None).count()
            missing_tags = db.query(Product).filter(Product.tags == None).count()
            
            # Per il mapping, prendiamo le intestazioni dal primo foglio per la UI
            first_sheet = sheets_list[0] if sheets_list else "Sheet1"
            res_h = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{first_sheet}'!A1:Z1").execute()
            headers = res_h.get("values", [[]])[0]
            
            return {
                "sheet_name": sheet_name, 
                "sheets_list": sheets_list,
                "row_count": total_db, 
                "column_count": len(headers),
                "headers": headers,
                "ai_tasks_pending": missing_title + missing_tags,
                "missing_desc_count": missing_title,
                "missing_tags_count": missing_tags,
                "ready_products": ready_db
            }
        finally:
            db.close()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/ollama/status")
async def get_ollama_status():
    import ollama_bridge
    is_active = await ollama_bridge.check_ollama_status()
    models = await ollama_bridge.list_local_models()
    return {"active": is_active, "count": len(models), "models": models}

from fastapi.responses import HTMLResponse, StreamingResponse

@app.get("/api/ollama/stream-pull")
async def stream_pull_ollama_model(name: str):
    import ollama_bridge
    return StreamingResponse(ollama_bridge.stream_install_local_model(name), media_type="text/event-stream")

@app.post("/api/ollama/pull")
async def pull_ollama_model(request: Request):
    data = await request.json()
    import ollama_bridge
    name = data.get("name", "llama3")
    success = await ollama_bridge.install_local_model(name)
    return {"status": "success" if success else "error"}

@app.post("/api/ollama/delete")
async def delete_ollama_model(request: Request):
    data = await request.json()
    import ollama_bridge
    name = data.get("name")
    if not name: return {"status": "error"}
    success = await ollama_bridge.uninstall_local_model(name)
    return {"status": "success" if success else "error"}

from fastapi.responses import HTMLResponse

@app.get("/auth/google/callback")
def google_callback(request: Request):
    try:
        base_url = str(request.base_url).rstrip('/')
        redirect_uri = f"{base_url}/auth/google/callback"
        state = request.query_params.get("state")
        google_auth.handle_callback(code=request.query_params.get("code"), url=redirect_uri, full_url=str(request.url), state=state)
        
        # Script per chiudere il popup OAuth e aggiornare la finestra madre
        html = """
        <script>
            if(window.opener) {
                window.opener.location.href = '/engine-room?success=google_connected';
                window.close();
            } else {
                window.location.href = '/engine-room?success=google_connected';
            }
        </script>
        """
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        import urllib.parse
        err_msg = urllib.parse.quote(str(e))
        html = f"""
        <script>
            if(window.opener) {{
                window.opener.location.href = '/engine-room?error=auth_failed&detail={err_msg}';
                window.close();
            }} else {{
                window.location.href = '/engine-room?error=auth_failed&detail={err_msg}';
            }}
        </script>
        """
        return HTMLResponse(content=html, status_code=200)


# --- ATELIER LAB ROUTING ---
class ValidationData(BaseModel):
    category: Optional[str] = None
    tags: Optional[str] = None
    material: Optional[str] = None
    hardware_type: Optional[str] = None
    color: Optional[str] = None
    condition_grade: Optional[str] = None
    price: Optional[str] = None

class ValidationAction(BaseModel):
    action: str
    data: Optional[ValidationData] = None

@app.get("/atelier-lab")
def atelier_lab(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    products = db.query(Product).filter(Product.status.in_([ProductStatus.Processing, ProductStatus.Validating])).all()
    context = {
        "request": request,
        "active_page": "atelier_lab",
        "auth_status": status,
        "all_systems_go": all_go,
        "products": products
    }
    return templates.TemplateResponse(request=request, name="atelier_lab.html", context=context)

@app.post("/api/lab/action/{product_id}")
def process_lab_action(product_id: int, payload: ValidationAction, db: Session = Depends(get_db)):
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item:
        return {"error": "Not found"}
        
    if payload.action == 'right': # Approva
        item.status = ProductStatus.Ready
        if payload.data:
            item.category = payload.data.category
            item.tags = payload.data.tags
            item.material = payload.data.material
            item.hardware_type = payload.data.hardware_type
            item.color = payload.data.color
            item.condition_grade = payload.data.condition_grade
            if payload.data.price:
                try: item.price = float(payload.data.price)
                except ValueError: pass
    elif payload.action == 'left': # Rifiuta
        item.status = ProductStatus.Draft
        
    # 'bottom' = Skip -> non cambiamo lo stato
    
    db.commit()
    return {"status": "ok"}

# --- THE MUSE ROUTING (INTERACTIVE) ---
@app.post("/api/muse/generate/{product_id}")
async def muse_generate(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    model_type = data.get("model", "llama3") # llama3, mistral, gpt4 etc    
    tone = data.get("tone", "luxury")
    instructions = data.get("instructions", "").strip()
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item:
        return {"error": "Prodotto non trovato"}
    
    # Costruzione Prompt Evoluto
    system_role = "Sei un copywriter esperto nel settore del lusso e della moda d'alta gamma."
    
    tone_prompts = {
        "luxury": "Usa un tono editoriale, sofisticato e raffinato. Esalta l'artigianalità e il prestigio del brand.",
        "storytelling": "Crea una narrazione evocativa ed emozionale. Racconta una storia che faccia sognare chi legge.",
        "technical": "Sii estremamente preciso e analitico. Concentrati su materiali, finiture, dimensioni e integrità strutturale.",
        "minimal": "Sii conciso ed essenziale. Usa un linguaggio pulito, moderno e 'quiet luxury' senza eccessivi aggettivi."
    }
    
    selected_tone_instr = tone_prompts.get(tone, tone_prompts["luxury"])
    
    prompt = (
        f"{selected_tone_instr}\n"
        f"Articolo: {item.brand} {item.model}\n"
        f"Specifiche: Materiale {item.material or 'N/D'}, Colore {item.color or 'N/D'}, Hardware {item.hardware_type or 'N/D'}, Condizioni {item.condition_grade or 'N/D'}.\n"
    )
    
    if instructions:
        prompt += f"Istruzioni Extra del Cliente: {instructions}\n"
        
    prompt += "\nComponi ora una descrizione accattivante in italiano per il nostro catalogo e-commerce di lusso."

    import ollama_bridge
    # 1. Carica configurazione task (Decidi chi parla)
    assigned_model = "llama3"
    import os
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cnf = json.load(f)
            assigned_model = cnf.get("task_assignments", {}).get("description", "llama3")
    
    model_to_call = data.get("model", assigned_model)
    response = await ollama_bridge.generate_narrative(model_to_call, prompt)
    return {"description": response, "model_used": model_to_call}

class MuseSaveData(BaseModel):
    description: str
    api_cost: Optional[float] = 0.001 # Costo stimato mock

@app.post("/api/muse/save/{product_id}")
def save_muse_description(product_id: int, payload: MuseSaveData, db: Session = Depends(get_db)):
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item:
        return {"error": "Not found"}
        
    item.ai_description_it = payload.description
    # Aggiorna i costi API tracciati per questo elemento
    if item.api_cost_usd is None: item.api_cost_usd = 0.0
    item.api_cost_usd += payload.api_cost
    
    # Lo sposta dalla coda The Muse verso Atelier Lab (Validating)
    item.status = ProductStatus.Validating
    db.commit()
    return {"status": "ok"}


@app.post("/api/settings/keys")
def save_keys(
    request: Request, 
    openai: str = Form(""),
    anthropic: str = Form(""),
    gemini: str = Form(""),
    serper: str = Form(""),
    shopify_token: str = Form(""),
    shopify_url: str = Form(""),
    db: Session = Depends(get_db)
):
    inputs = {
        "openai": openai,
        "anthropic": anthropic,
        "gemini": gemini,
        "serper": serper,
        "shopify_token": shopify_token,
        "shopify_url": shopify_url
    }
    
    for service, key in inputs.items():
        if key and not key.startswith("***") and "..." not in key:
            auth_manager.save_api_key(service, key)
            
            setting = db.query(Setting).filter(Setting.service_name == service).first()
            if not setting:
                setting = Setting(service_name=service, is_connected=1)
                db.add(setting)
            else:
                setting.is_connected = 1
    
    db.commit()
    return RedirectResponse(url="/engine-room", status_code=303)


# --- SHOPIFY BRIDGE ---
@app.post("/api/bridge/publish")
async def trigger_shop_publish(background_tasks: BackgroundTasks):
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    # In una app reale potremmo far girare questo come Background Task e mostrare progress
    # ma usiamo async/await qui direttamente così il JS attende e mostra il toast finito.
    result = await bridge.publish_ready_items()
    return result

# --- PIM ENTERPRISE PLACEHOLDERS (POC) ---
def render_placeholder(page_name: str, request: Request, db: Session):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": page_name, "auth_status": status, "all_systems_go": all_go}
    # Useremo un template generico di poc (se manca, cadrà su 500, quindi creiamo un mock template veloce dolo l'inserimento)
    return templates.TemplateResponse(request=request, name=f"{page_name}.html", context=context)

@app.post("/api/drive/list")
async def api_drive_list(request: Request):
    data = await request.json()
    parent_id = data.get("parent_id", "root")
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        q = f"'{parent_id}' in parents and trashed=false"
        results = service.files().list(q=q, fields="files(id, name, mimeType, size)", pageSize=1000, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        return {"files": results.get("files", [])}
    except Exception as e:
        return {"error": str(e)}

@app.get("/drive-explorer")
def drive_explorer(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    creds = google_auth.get_credentials()
    drive_files = []
    stats = {"heic": 0, "jpeg": 0, "total": 0}
    
    if creds:
        try:
            from googleapiclient.discovery import build
            service = build('drive', 'v3', credentials=creds)
            
            # Recuperiamo la cartella sorgente dal config
            cnf = {}
            if os.path.exists("workspace_config.json"):
                with open("workspace_config.json", 'r') as f:
                    cnf = json.load(f)
            root_id = cnf.get("folder_id")
            
            if root_id:
                print(f"📡 [Drive] Scansione mirata su folder_id: {root_id}")
                
                # 1. Recuperiamo prima i figli diretti (per statistiche base e sottocartelle)
                # Google Drive API non supporta una ricerca ricorsiva profonda in una singola query 'q',
                # ma per ora ci limitiamo alla root e ai suoi figli diretti per precisione chirurgica.
                q_strict = f"'{root_id}' in parents and trashed = false and (mimeType contains 'image/' or mimeType = 'application/vnd.google-apps.folder' or name contains '.heic' or name contains '.HEIC')"
                
                results = service.files().list(
                    q=q_strict,
                    fields="files(id, name, mimeType, size)",
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
                
                all_found = results.get('files', [])
                
                # Filtriamo i file (HEIC e JPEG) e identifichiamo eventuali sottocartelle
                images_in_root = [f for f in all_found if 'image/' in f.get('mimeType','') or f['name'].lower().endswith(('.heic', '.jpg', '.jpeg'))]
                subfolders = [f for f in all_found if f.get('mimeType') == 'application/vnd.google-apps.folder']
                
                # Se vogliamo essere realmente accurati sui 670 file, facciamo un secondo round veloce per i figli delle sottocartelle
                all_images = list(images_in_root)
                if subfolders:
                    for sf in subfolders[:10]: # Limitiamo alle prime 10 sottocartelle per velocità UI
                        q_sub = f"'{sf['id']}' in parents and trashed = false and (mimeType contains 'image/' or name contains '.heic')"
                        sub_res = service.files().list(q=q_sub, fields="files(id, name, mimeType, size)", pageSize=500, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
                        all_images.extend(sub_res.get('files', []))

                # De-duplicazione per evitare falsi positivi (es. shortcut multipli o upload doppi identici)
                seen_keys = set()
                unique_images = []
                for img in all_images:
                    file_id = img['id']
                    sig = (img['name'], img.get('size', ''))
                    
                    if file_id not in seen_keys and sig not in seen_keys:
                        seen_keys.add(file_id)
                        seen_keys.add(sig)
                        unique_images.append(img)
                
                all_images = unique_images

                stats["total"] = len(all_images)
                stats["heic"] = len([f for f in all_images if 'heic' in f.get('mimeType','').lower() or f['name'].lower().endswith('.heic')])
                stats["jpeg"] = len([f for f in all_images if 'jpeg' in f.get('mimeType','').lower() or f['name'].lower().endswith('.jpg')])
                
                # Applicazione filtro richiesto dalla UI (default: heic)
                filter_type = request.query_params.get("filter", "heic")
                filtered_images = []
                for f in all_images:
                    is_heic = 'heic' in f.get('mimeType','').lower() or f['name'].lower().endswith('.heic')
                    is_jpeg = 'jpeg' in f.get('mimeType','').lower() or f['name'].lower().endswith(('.jpg', '.jpeg'))
                    
                    if filter_type == "heic" and is_heic: filtered_images.append(f)
                    elif filter_type == "jpeg" and is_jpeg: filtered_images.append(f)
                    elif filter_type == "all": filtered_images.append(f)
                
                drive_files = filtered_images[:100] # Limite UI
                print(f"📊 [Drive] Trovati {stats['total']} asset, mostrati {len(drive_files)} ({filter_type}).")
                
        except Exception as e:
            print("Drive Explorer Error:", e)

    context = {
        "request": request,
        "active_page": "drive_explorer",
        "auth_status": status,
        "all_systems_go": all_go,
        "files": drive_files,
        "stats": stats,
        "filter_type": request.query_params.get("filter", "heic")
    }
    return templates.TemplateResponse(
        request=request, name="drive_explorer.html", context=context
    )

@app.post("/api/drive/convert")
async def drive_convert_bulk(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    file_ids = data.get("ids", [])
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    # Avviamo il lavoro pesante in background per non bloccare il server
    background_tasks.add_task(process_conversions, file_ids, creds)
    return {"status": "finished", "message": "Sviluppo avviato in background. Controlla la cartella 'convertite' tra poco."}

def process_conversions(file_ids, creds):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    from PIL import Image
    from pillow_heif import register_heif_opener
    import io, json, os
    register_heif_opener()
    
    print(f"🚀 [Lab] Avvio conversione batch per {len(file_ids)} file...")
    
    # Recuperiamo il target folder out id dal config
    cnf = {}
    if os.path.exists("workspace_config.json"):
        with open("workspace_config.json", 'r') as f:
            cnf = json.load(f)
    
    target_root_id = cnf.get("folder_out_id")
    drive_service = build('drive', 'v3', credentials=creds)
    
    for file_id in file_ids:
        try:
            # 1. Recupero Testata e Padre originale
            meta = drive_service.files().get(fileId=file_id, fields="name,parents", supportsAllDrives=True).execute()
            parents = meta.get('parents', [])
            if not parents: continue
            
            source_parent_id = parents[0]
            parent_meta = drive_service.files().get(fileId=source_parent_id, fields="name", supportsAllDrives=True).execute()
            folder_name = parent_meta.get("name")
            
            # 2. Creazione/Individuazione Cartella Specchio in COMPLETE
            target_folder_id = target_root_id or source_parent_id
            if target_root_id:
                print(f"📂 [Mirror] Verifico cartella '{folder_name}' in Complete...")
                q = f"name = '{folder_name}' and '{target_root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                res = drive_service.files().list(q=q, fields="files(id)", supportsAllDrives=True).execute()
                found = res.get("files", [])
                if found:
                    target_folder_id = found[0]['id']
                else:
                    print(f"📁 [Mirror] Creo nuova cartella '{folder_name}' in Complete...")
                    new_f = drive_service.files().create(body={
                        'name': folder_name, 
                        'mimeType': 'application/vnd.google-apps.folder', 
                        'parents': [target_root_id]
                    }, fields='id', supportsAllDrives=True).execute()
                    target_folder_id = new_f['id']
            
            # 3. Conversione HEIC -> JPEG
            print(f"🖼️ [HeicEngine] Sviluppo in corso: {meta['name']}...")
            raw_data = drive_service.files().get_media(fileId=file_id).execute()
            img = Image.open(io.BytesIO(raw_data))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            out.seek(0)
            
            media = MediaIoBaseUpload(out, mimetype='image/jpeg')
            new_name = meta['name'].rsplit('.', 1)[0] + ".jpg"
            
            drive_service.files().create(body={
                'name': new_name, 
                'parents': [target_folder_id]
            }, media_body=media, fields='id', supportsAllDrives=True).execute()
            print(f"✅ [HeicEngine] Inviato a Drive: {new_name}")
            
        except Exception as e:
            print(f"❌ [Lab Error] Conversione fallita per {file_id}: {e}")
            
    print("🏁 [Lab] Batch di sviluppo completato.")

@app.get("/api/drive/proxy/{file_id}")
def drive_proxy(file_id: str):
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import httpx
    import io
    
    # La funzione sync (def) permette a FastAPI di gestirla in un thread separato
    drive_service = build('drive', 'v3', credentials=creds)
    try:
        f = drive_service.files().get(fileId=file_id, fields="thumbnailLink", supportsAllDrives=True).execute()
        link = f.get("thumbnailLink")
        
        if link:
            # Recupero thumbnail con client sync per evitare di bloccare l'async loop
            with httpx.Client(follow_redirects=True, timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {creds.token}", "User-Agent": "Atelier/1.0"}
                resp = client.get(link.replace("=s220", "=s800"), headers=headers)
                if resp.status_code == 200:
                    return StreamingResponse(io.BytesIO(resp.content), media_type="image/jpeg")

        # Fallback
        res = drive_service.files().get_media(fileId=file_id).execute()
        return StreamingResponse(io.BytesIO(res), media_type="image/jpeg")
    except Exception as e:
        return StreamingResponse(io.BytesIO(b""), media_type="image/png")

@app.get("/system-logs")
def system_logs(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "system_logs", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse(
        request=request, name="system_logs.html", context=context
    )

@app.get("/api/snapshots")
def get_snapshots():
    import os
    if not os.path.exists("snapshots"): os.makedirs("snapshots")
    items = []
    for f in os.listdir("snapshots"):
        if f.endswith(".db"):
            path = os.path.join("snapshots", f)
            stats = os.stat(path)
            items.append({
                "name": f,
                "date": datetime.datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": round(stats.st_size / 1024, 1)
            })
    return sorted(items, key=lambda x: x['date'], reverse=True)

@app.post("/api/snapshots/create")
def create_snapshot():
    import shutil
    import os
    import datetime
    if not os.path.exists("snapshots"): os.makedirs("snapshots")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"atelier_snapshot_{ts}.db"
    shutil.copy2("atelier_ai.db", f"snapshots/{filename}")
    return {"status": "ok", "filename": filename}

@app.post("/api/snapshots/restore/{filename}")
def restore_snapshot(filename: str):
    import shutil
    import os
    path = os.path.join("snapshots", filename)
    if os.path.exists(path):
        shutil.copy2(path, "atelier_ai.db")
        return {"status": "ok"}
    return {"status": "error", "message": "File non trovato"}
@app.get("/api/system/activity")
async def get_system_activity():
    import os
    log_file = "harvester_debug.log"
    if not os.path.exists(log_file):
        return {"logs": ["In attesa di attività..."]}
        
    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
            # Prendiamo le ultime 15 righe e le puliamo
            recent = [line.strip() for line in lines[-15:] if line.strip()]
            return {"logs": recent}
    except Exception as e:
        return {"logs": [f"Errore lettura log: {str(e)}"]}

@app.get("/api/drive/search-folder")
async def drive_search_folder(q: str):
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        query = f"mimeType = 'application/vnd.google-apps.folder' and name contains '{q}' and trashed = false"
        results = service.files().list(
            q=query, 
            fields="files(id, name)", 
            pageSize=10, 
            supportsAllDrives=True, 
            includeItemsFromAllDrives=True
        ).execute()
        return {"folders": results.get("files", [])}
    except Exception as e:
        return {"error": str(e)}

@app.get("/loom-mapping")
def loom_mapping(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "loom_mapping", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse(request=request, name="loom_mapping.html", context=context)

@app.get("/tag-central")
def tag_central(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    all_products = db.query(Product).all()
    
    # Mappa tag -> lista oggetti prodotto (per miniature)
    tag_map = {}
    for p in all_products:
        if p.tags:
            ts = [t.strip() for t in p.tags.split(',') if t.strip()]
            for t in ts:
                if t not in tag_map:
                    tag_map[t] = []
                # Prendiamo solo i dati necessari per la UI
                images = []
                if p.matched_images_json:
                    import json
                    try:
                        imgs = json.loads(p.matched_images_json)
                        if imgs: images.append(imgs[0].get("thumb") or imgs[0].get("link"))
                    except: pass
                
                tag_map[t].append({
                    "id": p.id,
                    "brand": p.brand,
                    "model": p.model,
                    "thumb": images[0] if images else None
                })
    
    sorted_tags = sorted(tag_map.items(), key=lambda x: len(x[1]), reverse=True)
    context = {
        "request": request, 
        "active_page": "tag_central", 
        "auth_status": status, 
        "all_systems_go": all_go, 
        "tags": sorted_tags
    }
    return templates.TemplateResponse(request=request, name="tag_central.html", context=context)

@app.post("/api/tags/rename")
async def rename_tag(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    
    if not old_name or not new_name: return {"error": "Parametri mancanti"}
    
    # Aggiornamento massivo su tutti i prodotti
    products = db.query(Product).filter(Product.tags.like(f"%{old_name}%")).all()
    updated = 0
    for p in products:
        tag_list = [t.strip() for t in p.tags.split(',') if t.strip()]
        if old_name in tag_list:
            tag_list = [new_name if t == old_name else t for t in tag_list]
            p.tags = ", ".join(list(set(tag_list))) # Uniq
            updated += 1
    
    db.commit()
    return {"status": "ok", "updated": updated}

@app.post("/api/tags/delete")
async def delete_tag(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    tag_name = data.get("name")
    if not tag_name: return {"error": "Nome mancante"}
    
    products = db.query(Product).filter(Product.tags.like(f"%{tag_name}%")).all()
    updated = 0
    for p in products:
        tag_list = [t.strip() for t in p.tags.split(',') if t.strip()]
        if tag_name in tag_list:
            tag_list = [t for t in tag_list if t != tag_name]
            p.tags = ", ".join(tag_list) if tag_list else None
            updated += 1
            
    db.commit()
    return {"status": "ok", "updated": updated}

@app.get("/prompt-vault")
def prompt_vault(request: Request, db: Session = Depends(get_db)): return render_placeholder("prompt_vault", request, db)

@app.get("/media-vault")
def media_vault(request: Request, db: Session = Depends(get_db)): return render_placeholder("media_vault", request, db)

@app.get("/finance")
def finance(request: Request, db: Session = Depends(get_db)): return render_placeholder("finance", request, db)

@app.get("/shopify-routing")
def shopify_routing(request: Request, db: Session = Depends(get_db)): return render_placeholder("shopify_routing", request, db)
