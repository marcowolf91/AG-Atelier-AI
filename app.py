# Version: 1.2-STABLE - Atelier Lab Fixed
from fastapi import FastAPI, Depends, Request, Form, BackgroundTasks, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text, String, and_, or_, func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from PIL import Image
from pillow_heif import register_heif_opener
import io, json, os, time, datetime, logging, re, shutil, uuid, httpx, asyncio
from concurrent.futures import ThreadPoolExecutor

# Aumentiamo il pool di thread per gestire centinaia di richieste proxy contemporanee
executor = ThreadPoolExecutor(max_workers=100)
asyncio.get_event_loop_policy().get_event_loop().set_default_executor(executor)
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import google_auth
from darkroom_utils import get_drive_service

CONFIG_FILE = "workspace_config.json"
DARKROOM_CACHE_FILE = "darkroom_cache.json"
THUMBNAIL_CACHE = {} # Cache in-memory per velocizzare il proxy

try:
    register_heif_opener()
except Exception as e:
    print(f"⚠️ [System] Errore inizializzazione HEIF: {e}")

# Database & Core
from database import engine, SessionLocal, Product, ProductStatus, get_db, Setting, CategoryGovernance, CategoryRule, ApiUsage
import auth_manager
import google_auth
from governance_engine import GovernanceEngine

app = FastAPI(title="Atelier AI")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

import json
def from_json(value):
    try:
        return json.loads(value)
    except:
        return []
templates.env.filters["from_json"] = from_json

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
        "shopify_token_val": auth_manager.get_raw_api_key("shopify_token"),
        "shopify_url_val": auth_manager.get_raw_api_key("shopify_url"),
        "shopify_client_id_val": auth_manager.get_raw_api_key("shopify_client_id"),
        "shopify_client_secret_val": auth_manager.get_raw_api_key("shopify_client_secret")
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
    # In Lab = Prodotti con immagini pronte
    validating = db.query(Product).filter(
        Product.matched_images_json != None,
        Product.matched_images_json != "[]",
        Product.matched_images_json != ""
    ).count()
    published = db.query(Product).filter(Product.status == ProductStatus.Published).count()
    errors = db.query(Product).filter(Product.status == ProductStatus.Error).count()
    
    # Consumo SERP Reale
    usage = db.query(ApiUsage).filter(ApiUsage.service_name == "serper").first()
    serp_used = usage.total_hits if usage else 0
    total_val_calc = 0.0 # Valore rimosso come richiesto

    status_sys, all_go = get_system_status()

    # Brand con conteggi per filtro
    brand_counts_raw = db.query(Product.brand, func.count(Product.id)).group_by(Product.brand).all()
    brands_with_counts = []
    for b_name, b_count in brand_counts_raw:
        if b_name:
            brands_with_counts.append({"name": b_name, "count": b_count})
    brands_with_counts = sorted(brands_with_counts, key=lambda x: x["name"])

    # --- SENTINEL DIAGNOSTICS ---
    no_price_count = db.query(Product).filter((Product.price == None) | (Product.price == 0)).count()
    no_images_count = db.query(Product).filter((Product.matched_images_json == None) | (Product.matched_images_json == "[]") | (Product.matched_images_json == "")).count()
    ai_errors_count = db.query(Product).filter(Product.status == ProductStatus.Error).count()

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
        "filter_status": status_filter,
        # Sentinel Flags
        "sentinel": {
            "no_price": no_price_count,
            "no_images": no_images_count,
            "ai_errors": ai_errors_count
        }
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
        
    products = query.order_by(Product.id.asc()).limit(1000).all()
    
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


@app.get("/api/harvester/status")
async def harvester_status(db: Session = Depends(get_db)):
    from harvester_state import PROCESS_PROGRESS
    # Prendiamo gli ultimi 5 prodotti arricchiti con successo per aggiornare la UI dinamicamente
    recent = db.query(Product).filter(Product.status.in_([ProductStatus.Ready, ProductStatus.Validating])).order_by(Product.updated_at.desc()).limit(10).all()
    recent_data = [{"id": p.id, "seo_title": p.seo_title, "score": p.match_confidence or 0} for p in recent]
    
    return {
        "batch_total": PROCESS_PROGRESS["total"],
        "batch_completed": PROCESS_PROGRESS["completed"],
        "recent_results": recent_data
    }

@app.get("/api/logs")
def get_logs():
    """Restituisce gli ultimi log dalla Sala Macchine per la console UI."""
    from logger_utils import LIVE_LOGS
    return {"logs": LIVE_LOGS}

@app.get("/api/harvester/batch-status")
def get_harvester_batch_status():
    from harvester_state import ENGINE_STATE, PROCESS_PROGRESS
    return {
        "engine": ENGINE_STATE,
        "overall": PROCESS_PROGRESS
    }

@app.post("/api/harvester/batch-next")
async def harvester_batch_next(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from harvester import HarvesterEngine, BATCH_ENGINE_STATE
    if BATCH_ENGINE_STATE["status"] not in ["WAITING_FOR_CONFIRMATION", "IDLE"]:
        return {"status": "error", "message": "Engine is already running or in invalid state"}
    
    engine = HarvesterEngine()
    background_tasks.add_task(engine.run_harvester)
    return {"status": "ok", "message": "Prossimo batch avviato."}

@app.post("/api/harvester/batch-apply")
async def harvester_batch_apply(request: Request, db: Session = Depends(get_db)):
    from harvester_state import ENGINE_STATE
    import json
    
    data_in = await request.json()
    choices = data_in.get("choices", {})
    edited_vals = data_in.get("edited_values", {})

    ids_to_apply = ENGINE_STATE["current_batch_ids"]
    if not ids_to_apply:
        return {"status": "error", "message": "Nessun prodotto nel batch attuale."}
        
    products = db.query(Product).filter(Product.id.in_(ids_to_apply)).all()
    for item in products:
        if item.raw_harvested_data:
            try:
                raw_data = json.loads(item.raw_harvested_data)
                if raw_data.get("status") == "PRELIMINARY":
                    sid = str(item.id)
                    my_choice = choices.get(sid, {})
                    my_vals = edited_vals.get(sid, {})
                    use_all = not choices

                    if use_all or my_choice.get("seo_title"): 
                        item.seo_title = my_vals.get("seo_title", raw_data.get("proposed_seo_title", raw_data.get("seo_title", item.seo_title)))
                    if use_all or my_choice.get("material"): 
                        item.material = my_vals.get("material", raw_data.get("proposed_material", raw_data.get("material", item.material)))
                    if use_all or my_choice.get("dimensions"): 
                        item.dimensions = my_vals.get("dimensions", raw_data.get("proposed_dimensions", raw_data.get("dimensions", item.dimensions)))
                    if use_all or my_choice.get("ai_description_it"): 
                        item.ai_description_it = my_vals.get("ai_description_it", raw_data.get("proposed_description", raw_data.get("ai_description_it", item.ai_description_it)))
                    
                    if use_all or my_choice.get("tags"):
                        user_tags = my_vals.get("tags")
                        if user_tags is not None:
                            item.tags = user_tags
                        else:
                            tags_data = raw_data.get("proposed_tags", raw_data.get("tags", []))
                            item.tags = ", ".join(tags_data) if isinstance(tags_data, list) else tags_data
                        
                    item.status = ProductStatus.Ready
                    item.raw_harvested_data = f"Certified Audit: {datetime.datetime.now()}"
            except Exception as e:
                print(f"Error applying product {item.id}: {str(e)}")
    
    try:
        db.commit()
        
        # RESET STATO ENGINE per evitare loop Spotlight
        ENGINE_STATE["current_batch_ids"] = []
        ENGINE_STATE["processed_count"] = 0
        
        if ENGINE_STATE["status"] != "RUNNING":
            if not ENGINE_STATE["pending_ids"]:
                ENGINE_STATE["status"] = "FINISHED"
            else:
                ENGINE_STATE["status"] = "IDLE" # Pronto per il prossimo batch
            
        return {"status": "ok", "message": f"Dati certificati per {len(products)} prodotti."}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"Errore salvataggio: {str(e)}"}

@app.get("/api/harvester/batch-details")
def get_harvester_batch_details(db: Session = Depends(get_db)):
    from harvester_state import ENGINE_STATE
    import json
    
    ids = ENGINE_STATE["current_batch_ids"]
    print(f"📦 [BatchDetails] Current IDs in engine: {ids}")
    if not ids:
        return {"status": "ok", "products": []}
        
    products = db.query(Product).filter(Product.id.in_(ids)).all()
    print(f"📦 [BatchDetails] Found {len(products)} products in DB for these IDs.")
    results = []
    for p in products:
        preliminary = {}
        if p.raw_harvested_data:
            try:
                data = json.loads(p.raw_harvested_data)
                if data.get("status") == "PRELIMINARY":
                    preliminary = data
            except: pass
        
        results.append({
            "id": p.id,
            "source_sheet": p.source_sheet or "",
            "original_sheets_row": p.original_sheets_row or p.id,
            "brand": p.brand or "",
            "model": p.model or "",
            "current": {
                "seo_title": p.seo_title or "",
                "material": p.material or "",
                "dimensions": p.dimensions or "",
                "ai_description_it": p.ai_description_it or "",
                "tags": p.tags or ""
            },
            "preliminary": preliminary
        })
    
    if not results:
        ENGINE_STATE["status"] = "IDLE"
        
    return {"status": "ok", "products": results}

@app.get("/the-darkroom/convert")
def darkroom_convert(request: Request, ids: str = ""):
    return templates.TemplateResponse(request=request, name="the_darkroom_convert.html", context={"file_ids": ids, "active_page": "darkroom_convert"})

@app.post("/api/darkroom/check-associations")
async def check_image_associations(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    ids = data.get("ids", [])
    if not ids: return {"associated": []}
    results = db.execute(text("SELECT matched_images_json FROM products WHERE matched_images_json IS NOT NULL AND matched_images_json != '' AND matched_images_json != '[]'")).all()
    taken_ids = set()
    for row in results:
        try:
            p_ids = json.loads(row[0])
            if isinstance(p_ids, list):
                for pid in p_ids:
                    if str(pid) in ids: taken_ids.add(str(pid))
        except: continue
    return {"associated": list(taken_ids)}

@app.get("/the-darkroom/matching")
def darkroom_matching(request: Request, ids: str = ""):
    return templates.TemplateResponse(request=request, name="the_darkroom_matching.html", context={"file_ids": ids, "active_page": "darkroom_matching"})

@app.post("/api/darkroom/convert-image")
async def api_convert_image(request: Request):
    file_id = request.query_params.get("file_id")
    if not file_id: return {"status": "error", "message": "Missing file_id"}
    
    drive_service = get_drive_service()
    if not drive_service: return {"status": "error", "message": "Auth required"}
    
    try:
        # Carico configurazione per cartella di destinazione
        cnf = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                cnf = json.load(f)
        
        target_root_id = cnf.get("folder_out_id")
        
        # Download meta
        meta = drive_service.files().get(fileId=file_id, fields="id, name, parents", supportsAllDrives=True).execute()
        
        # Logica di destinazione (Deep Mirroring)
        source_parent_id = meta.get('parents', [None])[0]
        target_folder_id = target_root_id or source_parent_id
        
        # Carico la radice sorgente per sapere dove fermarmi con il mirroring
        source_root_id = cnf.get("folder_id")

        if target_root_id and source_parent_id and source_root_id:
            try:
                # Risalgo la gerarchia dal file fino alla radice sorgente per costruire il percorso
                path_folders = []
                curr_id = source_parent_id
                while curr_id and curr_id != source_root_id:
                    f_meta = drive_service.files().get(fileId=curr_id, fields='id, name, parents', supportsAllDrives=True).execute()
                    path_folders.insert(0, f_meta.get('name'))
                    parents = f_meta.get('parents', [])
                    curr_id = parents[0] if parents else None
                
                # Ora replico il percorso nella destinazione
                last_target_parent = target_root_id
                for folder_name in path_folders:
                    q = f"name = '{folder_name}' and '{last_target_parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                    res = drive_service.files().list(q=q, fields='files(id, description)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
                    existing = res.get('files', [])
                    if existing:
                        last_target_parent = existing[0]['id']
                    else:
                        f_body = {'name': folder_name, 'parents': [last_target_parent], 'mimeType': 'application/vnd.google-apps.folder'}
                        new_f = drive_service.files().create(body=f_body, fields='id', supportsAllDrives=True).execute()
                        last_target_parent = new_f['id']
                
                target_folder_id = last_target_parent
            except Exception as e:
                print(f"⚠️ Mirroring Path Error: {e}")
                target_folder_id = target_root_id
        
        # Download media
        raw_data = drive_service.files().get_media(fileId=file_id).execute()
        
        # Conversione (Grazie a register_heif_opener gestiamo HEIC)
        img = Image.open(io.BytesIO(raw_data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95, optimize=True)
        out.seek(0)
        
        # 4. Upload finale (con metadata di tracciabilità)
        file_metadata = {
            'name': filename.rsplit('.', 1)[0] + '.jpg',
            'parents': [target_folder_id],
            'description': f"Origine: {filename} (Sviluppato via Atelier AI)"
        }
        
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(out, mimetype='image/jpeg')
        created_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name',
            supportsAllDrives=True
        ).execute()
        
        # 5. LOG STORICO (Tracciabilità locale)
        history_file = 'darkroom_history.json'
        history = []
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r') as hf:
                    history = json.load(hf)
            except: history = []
        
        history.append({
            "timestamp": datetime.datetime.now().isoformat(),
            "original_name": filename,
            "original_id": file_id,
            "converted_name": created_file.get('name'),
            "converted_id": created_file.get('id'),
            "target_folder_id": target_folder_id
        })
        
        with open(history_file, 'w') as hf:
            json.dump(history, hf, indent=4)

        # INVALIDIAMO LA CACHE: Forziamo il sistema a rivedere il drive al prossimo caricamento
        if os.path.exists(DARKROOM_CACHE_FILE):
            os.remove(DARKROOM_CACHE_FILE)
            
        return {"status": "ok", "new_file_id": created_file.get('id')}
        
    except Exception as e:
        print(f"❌ Conversion Error ({file_id}): {e}")
        return {"status": "error", "message": str(e)}

@app.get("/the-darkroom/board")
def get_darkroom_board(request: Request):
    return templates.TemplateResponse(request=request, name="the_darkroom_board.html", context={})

@app.get("/the-darkroom")
def the_darkroom(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {
        "request": request, 
        "active_page": "the_darkroom",
        "auth_status": status,
        "all_systems_go": all_go
    }
    return templates.TemplateResponse(request=request, name="the_darkroom.html", context=context)

import imagehash
from PIL import Image
import io

def get_visual_hash(file_id, drive_service):
    """Genera un perceptual hash per un'immagine su Drive."""
    try:
        content = drive_service.files().get_media(fileId=file_id).execute()
        img = Image.open(io.BytesIO(content))
        # Usiamo pHash per somiglianza visiva robusta
        return str(imagehash.phash(img))
    except Exception as e:
        print(f"Hash Error for {file_id}: {e}")
        return None

def cluster_images(files):
    """Raggruppa le immagini in base all'orario di creazione (delta < 1 min)."""
    if not files: return []
    sorted_files = sorted(files, key=lambda x: x.get('createdTime', ''), reverse=True)
    clusters = []
    current_cluster = []
    import datetime
    for i, f in enumerate(sorted_files):
        if not current_cluster:
            current_cluster.append(f)
            continue
        last_f = current_cluster[-1]
        try:
            t_current = datetime.datetime.fromisoformat(f['createdTime'].replace('Z', '+00:00'))
            t_last = datetime.datetime.fromisoformat(last_f['createdTime'].replace('Z', '+00:00'))
            diff = abs((t_last - t_current).total_seconds())
            if diff < 60: # 1 minuto
                current_cluster.append(f)
            else:
                clusters.append(current_cluster)
                current_cluster = [f]
        except:
            clusters.append(current_cluster)
            current_cluster = [f]
    if current_cluster:
        clusters.append(current_cluster)
    return clusters

def save_darkroom_cache(raw_files):
    """Salva la cache dei file includendo gli hash visivi."""
    try:
        with open(DARKROOM_CACHE_FILE, 'w') as f:
            json.dump({"timestamp": time.time(), "raw_files": raw_files}, f)
    except Exception as e:
        print(f"Error saving cache: {e}")

@app.get("/api/darkroom/scan-progress")
def get_scan_progress(db: Session = Depends(get_db)):
    """Restituisce quante immagini hanno già un hash visivo."""
    all_files = get_darkroom_images(refresh=False, db=db)
    hashed_count = sum(1 for f in all_files if f.get('visual_hash'))
    return {
        "total": len(all_files),
        "hashed": hashed_count,
        "percent": (hashed_count / len(all_files) * 100) if all_files else 0
    }

@app.post("/api/darkroom/start-scan")
async def start_visual_scan(batch_size: int = 50, db: Session = Depends(get_db)):
    """Avvia una sessione di hashing visivo per le immagini mancanti."""
    all_files = get_darkroom_images(refresh=False, db=db)
    
    creds = google_auth.get_credentials()
    from googleapiclient.discovery import build
    drive_service = build('drive', 'v3', credentials=creds)
    
    count = 0
    modified = False
    for f in all_files:
        if not f.get('visual_hash'):
            h = get_visual_hash(f['id'], drive_service)
            if h:
                f['visual_hash'] = h
                count += 1
                modified = True
            if count >= batch_size: break
            
    if modified:
        save_darkroom_cache(all_files)
        
    return {"processed": count, "remaining": sum(1 for f in all_files if not f.get('visual_hash'))}

def cluster_images_visual(files):
    """Raggruppa le immagini in base alla somiglianza visiva (pHash)."""
    if not files: return []
    import imagehash
    
    hash_map = {} # hash -> [files]
    for f in files:
        h = f.get('visual_hash', 'unknown')
        if h == 'unknown':
            # Se non ha hash, lo mettiamo in un gruppo temporaneo per orario
            continue
            
        found = False
        for master_h in hash_map.keys():
            if master_h != "unknown":
                dist = imagehash.hex_to_hash(h) - imagehash.hex_to_hash(master_h)
                if dist < 10: 
                    hash_map[master_h].append(f)
                    found = True
                    break
        if not found: hash_map[h] = [f]
        
    # Gestiamo i file senza hash raggruppandoli per tempo come fallback
    unknowns = [f for f in files if not f.get('visual_hash')]
    if unknowns:
        temp_clusters = cluster_images(unknowns)
        return list(hash_map.values()) + temp_clusters
        
    return list(hash_map.values())

@app.get("/api/darkroom/batches")
async def get_darkroom_batches(refresh: bool = False, db: Session = Depends(get_db)):
    """Restituisce le immagini raggruppate con logica VISIVA (Fase 1 reale)."""
    all_files = get_darkroom_images(refresh=refresh, db=db)
    
    # Filtro associati
    results = db.execute(text("SELECT matched_images_json FROM products WHERE matched_images_json IS NOT NULL AND matched_images_json != '' AND matched_images_json != '[]'")).all()
    taken_ids = set()
    for row in results:
        try:
            p_ids = json.loads(row[0])
            if isinstance(p_ids, list):
                for pid in p_ids: taken_ids.add(str(pid))
        except: continue
    
    free_files = [f for f in all_files if str(f['id']) not in taken_ids]
    
    # Clustering Visivo
    batches = cluster_images_visual(free_files)
    
    formatted_batches = []
    for i, b in enumerate(batches):
        formatted_batches.append({
            "id": f"batch_{i}",
            "count": len(b),
            "timestamp": b[0].get('createdTime'),
            "images": b,
            "leader": b[0]
        })
        
    return formatted_batches

@app.get("/drive-test")
def drive_test_page(request: Request):
    return templates.TemplateResponse(request=request, name="drive_test.html", context={})

@app.get("/api/darkroom/images")
def get_darkroom_images(refresh: bool = False, db: Session = Depends(get_db)):
    import time
    all_files = []
    now_ts = time.time()
    # 1. Caricamento da disco
    if not refresh and os.path.exists(DARKROOM_CACHE_FILE):
        try:
            with open(DARKROOM_CACHE_FILE, 'r') as f:
                cache_data = json.load(f)
                last_ts = cache_data.get("timestamp", 0)
                if (now_ts - last_ts) < 7200:
                    all_files = cache_data.get("raw_files", [])
                    if all_files:
                        print(f"⚡ [Cache OK] Ripristinati {len(all_files)} asset dal disco.")
        except Exception as e:
            print(f"⚠️ Errore lettura cache: {e}")
    
    # 2. Se vuoto o refresh, vai su Google
    if not all_files:
        with open("scratch/drive_debug.log", "a") as log_f:
            log_f.write(f"🔍 Scansione profonda. CWD: {os.getcwd()}\n")
            if not os.path.exists("token.json"):
                log_f.write("❌ ERRORE: token.json NON TROVATO!\n")
            else:
                log_f.write("✅ token.json presente.\n")
            
        creds = google_auth.get_credentials()
        if not creds: return []
        
        folder_in = None
        folder_out = None
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                cnf = json.load(f)
                folder_in = cnf.get("folder_id")
                folder_out = cnf.get("folder_out_id")
        
        target_roots = []
        if folder_in: target_roots.append(folder_in)
        if folder_out: target_roots.append(folder_out)
        
        if not target_roots: return []

        from googleapiclient.discovery import build
        drive_service = build('drive', 'v3', credentials=creds)
        
        with open("scratch/drive_debug.log", "w") as log_f:
            log_f.write(f"--- DRIVE SCAN DEBUG {datetime.datetime.now()} ---\n")
            log_f.write(f"Target Roots: {target_roots}\n")

        all_target_folders = []
        
        def get_folders_recursive(rid, depth=0):
            if depth > 3: return [rid] # Limite sicurezza
            flds = [rid]
            try:
                q_f = f"mimeType = 'application/vnd.google-apps.folder' and trashed = false and '{rid}' in parents"
                res_f = drive_service.files().list(q=q_f, fields="files(id)", supportsAllDrives=True).execute()
                for ff in res_f.get("files", []):
                    flds.extend(get_folders_recursive(ff['id'], depth + 1))
            except: pass
            return flds

        for root_id in target_roots:
            folders_found = get_folders_recursive(root_id)
            all_target_folders.extend(folders_found)
            with open("scratch/drive_debug.log", "a") as log_f:
                log_f.write(f"Root {root_id} found {len(folders_found)} subfolders\n")

        # Limitiamo a 100 cartelle per non eccedere la lunghezza della query string di Google
        all_target_folders = list(dict.fromkeys(all_target_folders))
        if not all_target_folders:
            print("⚠️ Nessuna cartella trovata per la scansione.")
            return []
            
        try:
            # PRE-MAPPING CARTELLE: Recuperiamo TUTTE le cartelle del progetto
            folder_meta = {}
            page_token = None
            while True:
                f_res = drive_service.files().list(
                    q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    fields="nextPageToken, files(id, name, parents)",
                    pageSize=1000,
                    supportsAllDrives=True,
                    pageToken=page_token
                ).execute()
                for f in f_res.get('files', []):
                    folder_meta[f['id']] = {'name': f['name'], 'parents': f.get('parents', [])}
                page_token = f_res.get('nextPageToken')
                if not page_token: break
            
            # Carichiamo gli ID radice dal config per fermare la risalita
            with open(CONFIG_FILE, 'r') as f:
                conf = json.load(f)
            project_roots = [conf.get("folder_id"), conf.get("folder_out_id")]
            
            # Funzione per verificare se una cartella appartiene al progetto
            def is_in_project(fid):
                if not fid: return False
                if fid in project_roots: return True
                meta = folder_meta.get(fid)
                if not meta: return False
                p_id = meta['parents'][0] if meta['parents'] else None
                return is_in_project(p_id)

            path_cache = {}
            def get_full_path(fid):
                if not fid or fid in project_roots: return ""
                if fid in path_cache: return path_cache[fid]
                meta = folder_meta.get(fid)
                if not meta: return ""
                name = meta['name']
                p_id = meta['parents'][0] if meta['parents'] else None
                if not p_id or p_id in project_roots:
                    path_cache[fid] = name
                    return name
                parent_path = get_full_path(p_id)
                full = f"{parent_path} > {name}" if parent_path else name
                path_cache[fid] = full
                return full

            # SCANSIONE ASSETS: Prendiamo tutte le immagini a cui abbiamo accesso
            # e filtriamo in memoria quelle che appartengono alle nostre cartelle radice
            page_token = None
            all_drive_files = []
            while True:
                res = drive_service.files().list(
                    q="mimeType contains 'image/' and trashed = false", 
                    fields="nextPageToken, files(id, name, mimeType, size, thumbnailLink, createdTime, description, parents)", 
                    pageSize=1000, 
                    pageToken=page_token,
                    orderBy="createdTime desc",
                    supportsAllDrives=True
                ).execute()
                
                batch = res.get("files", [])
                for f in batch:
                    if f.get('parents'):
                        p_id = f['parents'][0]
                        if is_in_project(p_id):
                            full_p = get_full_path(p_id) or "Root"
                            f['parent_name'] = full_p
                            f['folder_context'] = full_p.upper()
                            all_drive_files.append(f)
                
                page_token = res.get("nextPageToken")
                if not page_token: break
            
            all_files = all_drive_files
            
            with open("scratch/drive_debug.log", "a") as log_f:
                log_f.write(f"Assets found: {len(all_files)}\n")
            
            # FILTRO INTELLIGENTE: Se esiste un JPG e un HEIC/PNG con lo stesso nome per lo STESSO ID (raro) o stessa logica di sviluppo
            # Non dobbiamo collassare file diversi con lo stesso nome (es. più foto della stessa riga)
            file_map = {} 
            
            # Ordiniamo per estensione in modo che i JPG abbiano la precedenza se abbiamo duplicati HEIC/JPG dello stesso scatto
            # Usiamo una combinazione di nome base + dimensione o altro per distinguere scatti diversi
            sorted_files = sorted(all_files, key=lambda x: (1 if x['name'].lower().endswith(('.jpg', '.jpeg')) else 2))
            
            final_files = []
            seen_bases = {} # base_name -> list of ids
            
            for f_obj in sorted_files:
                raw_name = f_obj['name'].rsplit('.', 1)[0]
                base = raw_name.lower().strip()
                ext = f_obj['name'].rsplit('.', 1)[-1].lower()
                
                # Se è un HEIC/PNG e abbiamo già un JPG con lo stesso nome NELLA STESSA CARTELLA, scartiamo
                # Altrimenti lo teniamo come scatto unico
                is_duplicate = False
                if ext in ['heic', 'png', 'heif']:
                    for existing in final_files:
                        if existing['name'].rsplit('.', 1)[0].lower().strip() == base and \
                           existing.get('parent_name') == f_obj.get('parent_name') and \
                           existing['name'].lower().endswith(('.jpg', '.jpeg')):
                            is_duplicate = True
                            break
                
                if not is_duplicate:
                    final_files.append(f_obj)
            
            # ORDINAMENTO: Alfabetico per nome, così i numeri appaiono in ordine
            final_files.sort(key=lambda x: x['name'].lower())
            
            # MERGE: Preserviamo gli hash esistenti se presenti nella vecchia cache
            try:
                if os.path.exists(DARKROOM_CACHE_FILE):
                    with open(DARKROOM_CACHE_FILE, 'r') as old_f:
                        old_cache = json.load(old_f)
                        # Mappa id -> {hash, parent}
                        old_meta = {f['id']: {'h': f.get('visual_hash'), 'p': f.get('parent_name')} for f in old_cache.get("raw_files", [])}
                        for f in final_files:
                            meta = old_meta.get(f['id'])
                            if meta:
                                if meta['h']: f['visual_hash'] = meta['h']
                                if meta['p'] and not f.get('parent_name'): f['parent_name'] = meta['p']
            except: pass

            save_darkroom_cache(final_files)
            all_files = final_files 
        except Exception as e:
            print(f"❌ Errore Drive Scan: {e}")
            return []

    # 3. Check Associazioni dal DB (Sempre dinamico e veloce)
    associated_ids = set()
    try:
        all_matched = db.execute(text("SELECT matched_images_json FROM products WHERE matched_images_json IS NOT NULL")).fetchall()
        for row in all_matched:
            try:
                if row[0]:
                    ids = json.loads(row[0])
                    if isinstance(ids, list):
                        for item in ids:
                            if isinstance(item, dict) and "id" in item:
                                associated_ids.add(item["id"])
                            elif isinstance(item, str):
                                associated_ids.add(item)

            except: pass
    except Exception as e:
        print(f"⚠️ Errore lettura associazioni DB: {e}")

    valid_images = []
    import re
    try:
        for f in all_files:
            if f['mimeType'].startswith('application/'): continue
            f_copy = f.copy()
            f_copy['associated'] = f['id'] in associated_ids
            
            # LOGICA AUTO-MATCH RIGA + CONTESTO CARTELLA
            row_match = re.search(r'^(\d+)[_\-\s.]', f['name'])
            if not row_match:
                row_match = re.search(r'[_\-\s](\d+)[_\-\s.]', f['name'])

                
            if row_match:
                try:
                    f_copy['suggested_row'] = int(row_match.group(1))
                    f_copy['folder_context'] = (f.get('parent_name') or "").upper()
                except:
                    f_copy['suggested_row'] = None
                    f_copy['folder_context'] = None
            else:
                f_copy['suggested_row'] = None
                f_copy['folder_context'] = None
                
            valid_images.append(f_copy)
            if f.get('thumbnailLink'):
                THUMBNAIL_CACHE[f['id']] = f['thumbnailLink']
    except Exception as e:
        with open("scratch/drive_debug.log", "a") as f_log:
            f_log.write(f"❌ ERROR IN DARKROOM LOOP: {str(e)}\n")
        raise e

            
    valid_images.sort(key=lambda x: x.get('name', '').lower(), reverse=True)
    return valid_images



@app.get("/api/drive/thumbnail/{file_id}")
def drive_thumbnail(file_id: str):
    drive_service = get_drive_service()
    if not drive_service: return RedirectResponse(url=f"/api/drive/proxy/{file_id}")
    
    try:
        import httpx
        meta = drive_service.files().get(fileId=file_id, fields="thumbnailLink", supportsAllDrives=True).execute()
        thumb_url = meta.get("thumbnailLink")
        if not thumb_url:
             return RedirectResponse(url=f"/api/drive/proxy/{file_id}")
        
        # Otteniamo una versione a risoluzione più alta (s800 invece di s220)
        high_res_thumb = thumb_url.replace("=s220", "=s800")
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(high_res_thumb)
            return Response(content=resp.content, media_type="image/jpeg")
    except:
        return RedirectResponse(url=f"/api/drive/proxy/{file_id}")

@app.get("/api/darkroom/search-products")
def darkroom_search_products(q: str = "", only_pending: str = "false", db: Session = Depends(get_db)):

    try:
        query = db.query(Product)
        
        # Ricerca per parole chiave (AND tra le parole)
        keywords = q.strip().split()
        
        if not keywords: 
            # Se non ci sono keyword, mostriamo i primi 20 (priorità a quelli senza immagini se only_pending è true)
            if only_pending.lower() == "true":
                query = query.filter(or_(Product.matched_images_json == None, Product.matched_images_json == '[]', Product.matched_images_json == ''))
            return query.limit(20).all()
        
        conditions = []
        for word in keywords:
            word_query = f"%{word}%"
            conditions.append(
                or_(
                    Product.brand.ilike(word_query),
                    Product.model.ilike(word_query),
                    Product.sku.ilike(word_query),
                    Product.id.cast(String).ilike(word_query),
                    Product.category.ilike(word_query),
                    Product.description.ilike(word_query),
                    Product.color.ilike(word_query),
                    Product.dimensions.ilike(word_query),
                    Product.size.ilike(word_query),
                    Product.material.ilike(word_query)
                )
            )
        
        if conditions:
            query = query.filter(and_(*conditions))
            
        # Filtro di esclusione ultra-affidabile:
        # Mostriamo solo prodotti con campo immagini NULL o quasi vuoto (es. '', '[]')
        query = query.filter(
            or_(
                Product.matched_images_json == None,
                func.length(Product.matched_images_json) < 5
            )
        )
        
        return query.limit(50).all()
    except Exception as e:
        print(f"Search Error: {e}")
        return []

@app.get("/api/darkroom/find-matching-images")
async def find_matching_images(q: str, db: Session = Depends(get_db)):
    """Cerca immagini che corrispondono a un prodotto specifico (Product Radar)."""
    all_files = get_darkroom_images(refresh=False, db=db)
    
    # Filtro associati (già pronti)
    results = db.execute(text("SELECT matched_images_json FROM products WHERE matched_images_json IS NOT NULL AND matched_images_json != '' AND matched_images_json != '[]'")).all()
    taken_ids = set()
    for row in results:
        try:
            p_ids = json.loads(row[0])
            if isinstance(p_ids, list):
                for pid in p_ids: taken_ids.add(str(pid))
        except: continue
        
    free_files = [f for f in all_files if str(f['id']) not in taken_ids]
    
    query = q.lower()
    matching_ids = []
    
    # 1. Ricerca testuale semplice nei nomi file
    for f in free_files:
        if query in f['name'].lower():
            matching_ids.append(f['id'])
            
    # 2. Se abbiamo hash, potremmo espandere la ricerca (futuro)
    
    return {"matching_ids": matching_ids}

@app.get("/api/darkroom/analyze-vision")
async def darkroom_analyze_vision(file_id: str, product_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Analizza un'immagine con Gemini Vision usando i dati del Master come vincoli."""
    import google_auth
    from googleapiclient.discovery import build
    import base64
    import io
    
    api_key = auth_manager.get_raw_api_key("gemini")
    if not api_key: return {"error": "no_api_key"}

    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    drive_service = build('drive', 'v3', credentials=creds)
    try:
        # 1. Recuperiamo i dati del prodotto se forniti
        product_context = "Unknown Product"
        if product_id:
            p = db.query(Product).filter(Product.id == product_id).first()
            if p:
                product_context = f"Brand: {p.brand}, Model: {p.model}, Material: {p.material}, Color: {p.color}, Category: {p.category}"

        # 2. Scarichiamo e ottimizziamo l'immagine
        raw_data = drive_service.files().get_media(fileId=file_id).execute()
        from PIL import Image
        img = Image.open(io.BytesIO(raw_data))
        img.thumbnail((1024, 1024)) 
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        b64_img = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # 3. Chiamata a Gemini con il contesto del Master
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
        
        prompt = f"""You are an expert luxury authenticator. 
        GROUND TRUTH DATA FROM MASTER: {product_context}
        
        TASK:
        1. Look at the photo and verify it matches the GROUND TRUTH.
        2. Create a professional SEO Title. 
        3. CRITICAL RULE: Use the 'Model' from GROUND TRUTH as the base. DO NOT invent or guess sub-models (like 'Rockstud', 'City', 'Speed') if you don't see them clearly in the photo.
        4. If the master says 'Sneakers' and the photo shows a specific line name like 'VLTN' or 'Open', you can use it. But if you are unsure, stick to the Master Model.
        5. Return ONLY the new SEO Title in Italian.
        """
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}}
                ]
            }]
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30.0)
            res_data = resp.json()
            try:
                suggestion = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                return {"status": "ok", "suggestion": suggestion}
            except:
                return {"status": "error", "message": "Analisi fallita."}

    except Exception as e:
        return {"status": "error", "message": str(e)}

        # Suggeriamo un nome SEO pulito (slugify)
        import re
        seo_name = re.sub(r'[^a-z0-9]+', '-', suggestion.lower()).strip('-')
        
        return {
            "suggestion": suggestion,
            "seo_name": seo_name,
            "raw_analysis": result.strip()
        }
    except Exception as e:
        print(f"Vision Error: {e}")
        return {"error": str(e)}

@app.post("/api/darkroom/mass-associate-by-row")
async def mass_associate_by_row(request: Request, db: Session = Depends(get_db)):
    """Associa automaticamente le immagini, opzionalmente filtrando per una cartella specifica."""
    data = await request.json() if await request.body() else {}
    target_folder = (data.get("folder_context") or "").upper()
    
    all_files = get_darkroom_images(refresh=False, db=db)
    to_process = [f for f in all_files if not f.get('associated') and f.get('suggested_row')]
    
    # Filtro opzionale per cartella (Crumble Menu)
    if target_folder:
        to_process = [f for f in to_process if f.get('folder_context') == target_folder]
    
    if not to_process:
        return {"status": "info", "message": f"Nessuna immagine trovata {'nella sezione ' + target_folder if target_folder else ''}."}
        
    count = 0
    grouped = {} # (row, context) -> [file_ids]
    for f in to_process:
        key = (f['suggested_row'], f.get('folder_context', ''))
        if key not in grouped: grouped[key] = []
        grouped[key].append(f['id'])
        
    for (row_idx, context), fids in grouped.items():
        # Cerchiamo i prodotti con quella riga
        candidates = db.query(Product).filter(Product.original_sheets_row == row_idx).all()
        
        target_product = None
        if len(candidates) == 1:
            target_product = candidates[0]
        elif len(candidates) > 1 and context:
            # Disambiguazione per contesto cartella (es. "BORSE > DONNA" vs "BORSE DONNA")
            norm_context = context.replace(" > ", " ").replace("/", " ").upper()
            for p in candidates:
                p_context = (p.source_sheet or p.category or "").replace("/", " ").upper()
                if norm_context in p_context or p_context in norm_context or any(word in p_context for word in norm_context.split() if len(word) > 3):
                    target_product = p
                    break
                    
        if target_product:
            # Associazione
            current = []
            try:
                if target_product.matched_images_json:
                    current = json.loads(target_product.matched_images_json)
                    if not isinstance(current, list): current = []
            except: current = []
            
            new_list = list(set(current + fids))
            target_product.matched_images_json = json.dumps(new_list)
            target_product.status = "MATCHED"
            count += len(fids)
            
    db.commit()
    return {"status": "success", "message": f"Associazione contestualizzata: {count} immagini collegate."}

@app.post("/api/darkroom/associate-bulk")
async def darkroom_associate_bulk(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    file_ids = data.get("file_ids", [])
    product_id = data.get("product_id")
    seo_name = data.get("seo_name")
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    drive_service = get_drive_service()
    if not drive_service: return {"status": "error", "message": "Auth required"}
    
    try:
        current_images = json.loads(item.matched_images_json or "[]")
        
        for idx, file_id in enumerate(file_ids):
            # 1. Rinomina su Drive con suffisso sequenziale
            # Se è solo uno, niente suffisso. Se sono più di uno, -1, -2, etc.
            suffix = f"-{idx+1}" if len(file_ids) > 1 else ""
            final_name = f"{seo_name}{suffix}.jpg"
            
            drive_service.files().update(
                fileId=file_id, 
                body={"name": final_name}, 
                supportsAllDrives=True
            ).execute()
            
            # 2. Aggiunta alla lista del prodotto
            if file_id not in current_images:
                current_images.append(file_id)
        
        item.matched_images_json = json.dumps(current_images)
        db.commit()
        
        # Pulizia Cache per riflettere lo stato "associato"
        if os.path.exists(DARKROOM_CACHE_FILE):
            os.remove(DARKROOM_CACHE_FILE)
            
        return {"status": "ok", "associated": len(file_ids)}
    except Exception as e:
        print(f"❌ Bulk Association Error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/darkroom/associate")
async def darkroom_associate(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    data = await request.json()
    file_ids = data.get("file_ids", [])
    product_id = data.get("product_id")
    new_name = data.get("new_name") or data.get("seo_name")
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    try:
        # 1. Aggiornamento Immediato DB
        current_images = json.loads(item.matched_images_json or "[]")
        for fid in file_ids:
            if fid not in current_images:
                current_images.append(fid)
        
        item.matched_images_json = json.dumps(current_images)
        # Quando associamo le immagini, il prodotto entra ufficialmente nel "Lab" per la validazione
        item.status = ProductStatus.Validating
        
        db.commit()
        
        # 2. Rinomina su Drive in Background (per non bloccare la UI)
        background_tasks.add_task(rename_drive_files_task, file_ids, new_name)
        
        return {"status": "ok", "count": len(file_ids)}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

def rename_drive_files_task(file_ids, new_name):
    creds = google_auth.get_credentials()
    if not creds: return
    try:
        from googleapiclient.discovery import build
        drive_service = build('drive', 'v3', credentials=creds)
        for idx, fid in enumerate(file_ids):
            suffix = f"-{idx+1}" if len(file_ids) > 1 else ""
            ext = ".jpg"
            try:
                drive_service.files().update(
                    fileId=fid, 
                    body={"name": f"{new_name}{suffix}{ext}"}, 
                    supportsAllDrives=True
                ).execute()
                print(f"✅ Ridenominato: {fid} -> {new_name}{suffix}{ext}")
            except Exception as e:
                print(f"❌ Errore ridenominazione {fid}: {e}")
    except Exception as e:
        print(f"❌ Errore Task Ridenominazione: {e}")

@app.get("/api/darkroom/lab-ready")
def get_lab_ready(db: Session = Depends(get_db)):
    # Prodotti che hanno almeno una foto associata
    return db.query(Product).filter(Product.matched_images_json != None).all()

@app.post("/api/darkroom/convert")
async def darkroom_convert(request: Request, background_tasks: BackgroundTasks):
    # Alias per drive_convert_bulk ma specifico per Darkroom context
    return await drive_convert_bulk(request, background_tasks)

@app.get("/the-harvester")
def the_harvester(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    
    # Un prodotto è "Incompleto" se manca il titolo SEO, la descrizione o i Tag
    # Indipendentemente dal fatto che sia Ready o meno.
    products = db.query(Product).filter(
        (Product.status.in_([ProductStatus.Draft, ProductStatus.Error, ProductStatus.Processing, ProductStatus.Validating])) |
        (
            (Product.status == ProductStatus.Ready) & 
            (
                (Product.seo_title == None) | (Product.seo_title == "") |
                (Product.ai_description_it == None) | (Product.ai_description_it == "") |
                (Product.tags == None) | (Product.tags == "")
            )
        )
    ).order_by(Product.id.desc()).all()
    
    context = {"request": request, "active_page": "the_harvester", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse(request=request, name="the_harvester.html", context=context)

@app.post("/api/harvester/enrich_single/{pid}")
async def enrich_single(pid: int, db: Session = Depends(get_db)):
    """Lancia l'arricchimento AI su un singolo prodotto e restituisce i risultati live."""
    item = db.query(Product).filter(Product.id == pid).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    from harvester import HarvesterEngine
    engine = HarvesterEngine()
    
    # Eseguiamo in attesa per dare feedback immediato alla UI
    result = await engine.process_single_product(pid, db, model_choice="llama3")
    
    if "error" in result:
        return {"status": "error", "message": result["error"]}
        
    return {
        "status": "ok",
        "summary": {
            "seo_title": result.get("seo_title"),
            "tags": result.get("tags"),
            "material": result.get("material"),
            "dimensions": result.get("dimensions")
        }
    }

@app.get("/asset-vault")
def asset_vault(request: Request, db: Session = Depends(get_db)):
    status_sys, all_go = get_system_status()
    st_filter = request.query_params.get("status", "")
    
    query = db.query(Product)
    if st_filter == "ready":
        query = query.filter(Product.status == ProductStatus.Ready)
    elif st_filter == "published":
        query = query.filter(Product.status == ProductStatus.Published)
        
    products = query.order_by(Product.updated_at.desc()).all()
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
    products = await bridge.sync_catalog_with_shopify()
    return {"status": "ok", "products": products}

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
    # Ricalcolo integrità
    from harvester import HarvesterEngine
    product.match_confidence = HarvesterEngine.calculate_integrity(product)
    db.commit()
    return {"status": "ok"}

@app.post("/api/shopify/import")
async def import_shopify_product(request: Request):
    data = await request.json()
    mode = data.get("mode", "standard") # standard o enrich
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    # Passiamo il mode al bridge per gestire lo stato iniziale
    return await bridge.import_product_to_pim(data, mode=mode)

@app.post("/api/shopify/publish")
async def publish_shopify_product(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    sku = data.get("sku")
    product_id = data.get("product_id")
    
    # Lookup product
    product = None
    if product_id:
        # Gestione ID in formato GID (es: gid://shopify/Product/Local-2)
        if isinstance(product_id, str) and "Local-" in product_id:
            try:
                product_id = int(product_id.split("Local-")[-1])
            except: pass
        product = db.query(Product).filter(Product.id == product_id).first()

    elif sku and sku != "N/A":
        product = db.query(Product).filter(Product.sku == sku).first()
    
    if not product:
        return {"status": "error", "message": "Prodotto non trovato nel database PIM."}
        
    # VALIDAZIONE CRITICA: Impediamo la pubblicazione senza immagini
    if not product.matched_images_json or product.matched_images_json == "[]":
        return {"status": "error", "message": "Impossibile pubblicare: il prodotto non ha immagini associate nel PIM."}

    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    # Passiamo sia lo SKU che l'ID per sicurezza (il bridge userà lo SKU per Shopify)
    success = await bridge.publish_product_to_shopify(product.sku or f"SKU-{product.id}")
    
    if success:
        return {"status": "ok"}
    else:
        return {"status": "error", "message": "Errore durante la comunicazione con Shopify."}

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

@app.post("/api/vault/seo-rename-batch")
async def vault_seo_rename_batch(db: Session = Depends(get_db)):
    """Innesca la rinomina Deep per TUTTI i prodotti in stato READY che non sono ancora stati processati."""
    # Filtriamo i prodotti pronti
    products = db.query(Product).filter(Product.status == ProductStatus.Ready, Product.seo_title != None).all()
    
    if not products:
        return {"status": "ok", "message": "Nessun prodotto trovato in stato READY con Titolo SEO."}
        
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    renamed_total = 0
    errors = []
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        import re
        
        for product in products:
            try:
                base_name = product.seo_title.lower().replace(" ", "-").replace("/", "-")
                base_name = re.sub(r'[^a-z0-9\-]', '', base_name)
                
                # Rinomina Cartella
                if product.drive_folder_id:
                    service.files().update(
                        fileId=product.drive_folder_id,
                        body={"name": base_name.upper()},
                        supportsAllDrives=True
                    ).execute()
                    renamed_total += 1
                
                # Rinomina File
                img_meta = json.loads(product.matched_images_json) if product.matched_images_json else []
                files_to_process = [img for img in img_meta if img.get("type") == "image"]
                
                for idx, img in enumerate(files_to_process, start=1):
                    f_id = img.get("id")
                    if f_id:
                        ext = ".jpg"
                        if "." in img.get("name", ""):
                            ext = "." + img.get("name").split(".")[-1]
                        service.files().update(
                            fileId=f_id,
                            body={"name": f"{base_name}-{idx}{ext}"},
                            supportsAllDrives=True
                        ).execute()
                        renamed_total += 1
            except Exception as pe:
                errors.append(f"Errore SKU {product.sku}: {str(pe)}")
        
        return {
            "status": "ok", 
            "message": f"Batch Deep Rename completato: {renamed_total} elementi rinominati.",
            "errors": errors if errors else None
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

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

@app.get("/api/sync/status")
def get_sync_status():
    from sync_engine import SYNC_STATUS
    return SYNC_STATUS

async def run_background_sync(sid, target_sheet=None):
    from sync_engine import engine, SYNC_STATUS
    from database import SessionLocal
    import google_auth
    from googleapiclient.discovery import build
    from logger_utils import add_log
    
    db = SessionLocal()
    SYNC_STATUS["is_running"] = True
    SYNC_STATUS["current_sheet_idx"] = 0
    SYNC_STATUS["last_imported"] = 0
    SYNC_STATUS["last_updated"] = 0

    add_log(f"🔄 [The Loom] Avvio background task per: {target_sheet or 'Tutto il Master'}...")
    try:
        add_log(f"🔑 [The Loom] Recupero credenziali Google...")
        creds = google_auth.get_credentials()
        if not creds:
            add_log("❌ [The Loom] Errore: Credenziali Google mancanti o scadute.")
            SYNC_STATUS["is_running"] = False
            return
            
        add_log(f"🏗️ [The Loom] Inizializzazione API Google Sheets...")
        service = build('sheets', 'v4', credentials=creds)
        
        add_log(f"📡 [The Loom] Richiesta metadati per Sheet ID: {sid[:10]}...")
        spreadsheet = service.spreadsheets().get(spreadsheetId=sid).execute()
        sheets = spreadsheet.get('sheets', [])
        
        SYNC_STATUS["total_sheets"] = len(sheets)
        add_log(f"✅ [The Loom] Connessione riuscita: '{spreadsheet.get('properties', {}).get('title', 'Master Data')}'")
        add_log(f"🔎 [The Loom] Rilevati {len(sheets)} fogli di lavoro.")

        for idx, s in enumerate(sheets):
            s_title = s['properties']['title']
            
            # Aggiorniamo l'indice per mostrare il progresso della scansione
            SYNC_STATUS["current_sheet_idx"] = idx + 1
            SYNC_STATUS["current_sheet_name"] = s_title

            # Se è specificato un target_sheet, saltiamo gli altri (con confronto robusto)
            if target_sheet and s_title.strip().lower() != target_sheet.strip().lower():
                continue
            
            add_log(f"🧵 [The Loom] Elaborazione foglio: '{s_title}'...")
            try:
                res = await engine.sync_sheets(sid, db, range_name=f"'{s_title}'!A:Z")
                SYNC_STATUS["last_imported"] += res.get("imported", 0)
                SYNC_STATUS["last_updated"] += res.get("updated", 0)
                
                log_msg = f"✅ [The Loom] Foglio '{s_title}' completato: +{res.get('imported', 0)} nuovi"
                if res.get('updated', 0) > 0:
                    log_msg += f", {res.get('updated')} modifiche nel Master rilevate"
                add_log(log_msg + ".")
            except Exception as e_sheet:
                add_log(f"⚠️ [The Loom] Salto foglio '{s_title}': {e_sheet}")

        add_log(f"🏁 [The Loom] Sincronizzazione Master terminata.")
    except Exception as e:
        add_log(f"❌ [The Loom] Errore critico SYNC: {e}")
    finally:
        db.close()
        SYNC_STATUS["is_running"] = False

@app.get("/api/sync/conflict-details/{pid}")
def get_conflict_details(pid: int, db: Session = Depends(get_db)):
    item = db.query(Product).filter(Product.id == pid).first()
    if not item or not item.master_snapshot_json:
        return {"status": "error", "message": "Nessun dato di confronto trovato."}
    
    import json
    try:
        new_data = json.loads(item.master_snapshot_json)
        old_data = {
            "brand": item.brand,
            "model": item.model,
            "price": item.price,
            "description": item.description,
            "material": item.material,
            "color": item.color,
            "category": item.category
        }
        
        diff = []
        for key in new_data:
            old_val = str(old_data.get(key, ""))
            new_val = str(new_data.get(key, ""))
            if old_val != new_val:
                diff.append({
                    "field": key.replace("_", " ").capitalize(),
                    "old": old_val,
                    "new": new_val
                })
        
        return {"status": "ok", "diff": diff}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/sync/resolve-conflict/{pid}")
async def resolve_sync_conflict(pid: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    action = data.get("action") # "apply" or "ignore"
    
    item = db.query(Product).filter(Product.id == pid).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    if action == "apply":
        import json
        try:
            new_data = json.loads(item.master_snapshot_json)
            # Applichiamo i campi critici
            item.brand = new_data.get("brand", item.brand)
            item.model = new_data.get("model", item.model)
            item.price = new_data.get("price", item.price)
            item.description = new_data.get("description", item.description)
            item.material = new_data.get("material", item.material)
            item.color = new_data.get("color", item.color)
            item.category = new_data.get("category", item.category)
            
            item.has_master_conflict = 0
            
            # Ricalcolo integrità
            from logger_utils import add_log
            from harvester import HarvesterEngine
            item.match_confidence = HarvesterEngine.calculate_integrity(item)
            
            db.commit()
            return {"status": "ok", "message": "Dati Master applicati con successo"}
        except Exception as e:
            return {"status": "error", "message": f"Errore applicazione: {str(e)}"}
    else:
        # Ignore
        item.has_master_conflict = 0
        db.commit()
        return {"status": "ok", "message": "Modifica Master ignorata"}

@app.post("/api/sync")
async def trigger_sync(request: Request, background_tasks: BackgroundTasks):
    """Avvia la sync dal Google Sheet configurato in background."""
    config = get_settings()
    sid = config.get("sheet_id")
    if not sid: return {"status": "error", "message": "Sheet ID non configurato"}
    
    data = await request.json() if await request.body() else {}
    target = data.get("target_sheet")
    
    background_tasks.add_task(run_background_sync, sid, target)
    return {"status": "ok", "message": f"Sync {'Master' if not target else target} avviata"}


# --- ASSISTANT API ---
@app.post("/api/assistant/chat")
async def assistant_chat(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    user_msg = data.get("message", "").strip()
    if not user_msg: return {"response": "Come posso aiutarti?"}

    from ollama_bridge import generate_narrative
    
    # 1. Recupero statistiche base per dare contesto immediato
    total = db.query(Product).count()
    brands = db.query(Product.brand).distinct().count()
    missing_desc = db.query(Product).filter((Product.ai_description_it == None) | (Product.ai_description_it == "")).count()
    
    # Nuova metrica: Categorie
    categories_stats = db.query(Product.category).filter(Product.category != None).distinct().all()
    categories_list = [c[0] for c in categories_stats if c[0]]
    
    # Schema context for LLM
    schema_context = f"""
    Sei l'assistente Atelier AI. Hai accesso al database dei prodotti moda.
    Schema Tabella 'products': id, brand, model, category, ai_description_it, tags, status, price, confidence_score.
    Statistiche attuali:
    - Totale prodotti: {total}
    - Brand unici: {brands}
    - Categorie rilevate: {len(categories_list)} ({', '.join(categories_list[:5])}...)
    - Prodotti senza descrizione: {missing_desc}
    
    Rispondi in modo professionale ed elegante. Se l'utente chiede statistiche o dettagli sulle categorie (es. 'quante borse abbiamo?'), usa i dati sopra.
    Se chiede quanti prodotti mancano di qualcosa, riferisciti ai dati.
    """

    prompt = f"{schema_context}\n\nUtente: {user_msg}\nAssistente:"
    
    response = await generate_narrative("llama3", prompt)
    return {"response": response}

@app.post("/api/harvester/start")
async def start_harvester(request: Request):
    from harvester_state import ENGINE_STATE
    from logger_utils import add_log
    if ENGINE_STATE["status"] in ["RUNNING", "WAITING_FOR_CONFIRMATION"]:
        return {"status": "error", "message": "Processo già in corso o in attesa di revisione."}

    data = await request.json()
    ids = data.get("ids", [])
    
    import harvester
    import threading
    engine = harvester.HarvesterEngine()
    
    # Avviamo in un thread Python standard per non bloccare mai l'async loop di FastAPI
    # Questo è fondamentale per gestire operazioni DB sincrone e AI intense
    thread = threading.Thread(target=engine.run_harvester, args=(ids,))
    thread.daemon = True
    thread.start()
    
    add_log(f"⚡ [API] Thread Harvester lanciato per {len(ids) if ids else 'coda automatica'}")
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
        
        from harvester_state import ENGINE_STATE
        if pid in ENGINE_STATE.get("current_batch_ids", []):
            ENGINE_STATE["current_batch_ids"].remove(pid)
            
    return {"status": "ok"}

@app.get("/api/harvester/logs")
def get_harvester_logs():
    import harvester
    return {"logs": harvester.LIVE_LOGS}

@app.get("/engine-room")
async def engine_room(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    google_connected = google_auth.is_google_connected()
    
    # Pre-calculate health data for immediate feedback (SAFE MODE)
    import httpx
    # 1. DB
    try: 
        db.execute(text("SELECT 1"))
        db_health = {"ok": True, "val": "Connesso (SQLite)"}
    except: db_health = {"ok": False, "val": "Errore DB"}
    
    # 2. Drive
    drive_health = {"ok": google_connected, "val": "Sessione Attiva" if google_connected else "Scollegato"}
    
    # 3. AI
    ai_health = {"ok": False, "val": "Offline"}
    try:
        async with httpx.AsyncClient(timeout=0.6) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ai_health = {"ok": True, "val": "Ollama Attivo"}
    except: pass

    # 4. Shopify (Fast check)
    shopify_ok = False
    shopify_msg = "Scollegato"
    try:
        from shopify_bridge import ShopifyBridge
        bridge = ShopifyBridge()
        # Timeout brevissimo per non bloccare la pagina
        shopify_ok, shopify_msg = await bridge.check_connection()
    except: 
        shopify_msg = "Timeout o Errore"

    error = request.query_params.get("error")
    success = request.query_params.get("success")

    context = {
        "request": request,
        "active_page": "engine_room",
        "auth_status": status,
        "all_systems_go": all_go,
        "google_connected": google_connected,
        "error_msg": error,
        "success_msg": success,
        "health": {
            "db": db_health,
            "drive": drive_health,
            "ai": ai_health,
            "shopify": {"ok": shopify_ok, "msg": shopify_msg}
        }
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
            page_token = None
            while True:
                # Get child folders of src with pagination
                res = service.files().list(
                    q=f"'{src}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    fields="nextPageToken, files(id, name)",
                    pageSize=100,
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute()
                
                children = res.get("files", [])
                for child in children:
                    # Safety safeguard (increased to 1000)
                    if folders_created > 1000: break
                    
                    # Check if folder already exists in dst (escaping single quotes for query)
                    safe_name = child['name'].replace("'", "\\'")
                    existing_res = service.files().list(
                        q=f"'{dst}' in parents and name='{safe_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
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

                page_token = res.get('nextPageToken')
                if not page_token or folders_created > 1000:
                    break

        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, clone_recursive, source_id, target_id)
            
        return {"status": "success", "folders_created": folders_created}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/drive/sync")
async def sync_drive_images(db: Session = Depends(get_db)):
    """Matching Intelligente ad Alta Efficienza (SmartMapper) - Sheet Aware."""
    import google_auth
    creds = google_auth.get_credentials()
    if not creds: return {"error": "non_auth"}
    
    try:
        from googleapiclient.discovery import build
        service = build('drive', 'v3', credentials=creds)
        config = get_settings()
        
        # Supportiamo sia la vecchia che la nuova chiave di configurazione
        root_folder_id = config.get("drive_images_root_id") or config.get("folder_id")
        
        if not root_folder_id:
            return {"error": "Missing drive_images_root_id or folder_id in settings"}
            
        add_log(f"📡 [SmartMapper] Avvio Indicizzazione Contestuale (Root: {root_folder_id})")
        
        # 1. Mappatura dei Contesti (Cartelle dei Fogli)
        # Cerchiamo tutte le cartelle direttamente sotto la root
        q_root = f"'{root_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed=false"
        res_root = service.files().list(q=q_root, fields="files(id, name)").execute()
        sheet_folders = {f['name'].lower().strip(): f['id'] for f in res_root.get('files', [])}
        
        # Recuperiamo anche i file sciolti nella root per i prodotti senza foglio specifico
        q_files = f"'{root_folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
        res_files = service.files().list(q=q_files, fields="files(id, name, thumbnailLink, webViewLink, mimeType)").execute()
        root_inventory = res_files.get('files', [])
        
        # 2. Recupero Prodotti e Raggruppamento
        products = db.query(Product).all()
        matched_count = 0
        
        # Cache per gli inventari delle sottocartelle (per evitare doppie chiamate API per lo stesso foglio)
        folder_inventory_cache = {}

        for p in products:
            # PROTEZIONE: Non sovrascrivere mai associazioni manuali o già esistenti
            if p.matched_images_json and p.matched_images_json != "[]" and p.matched_images_json != "":
                continue
                
            # Determiniamo lo scope di ricerca
            target_inventory = []
            sheet_name = p.source_sheet.lower().strip() if p.source_sheet else None
            
            if sheet_name and sheet_name in sheet_folders:
                # Se abbiamo una cartella per questo foglio, usiamo quella
                f_id = sheet_folders[sheet_name]
                if f_id not in folder_inventory_cache:
                    add_log(f"📂 [SmartMapper] Scansione cartella contestuale: {p.source_sheet}")
                    # Scansione 1-level della cartella del foglio (immagini e sottocartelle prodotto)
                    q_sub = f"'{f_id}' in parents and trashed=false"
                    res_sub = service.files().list(
                        q=q_sub, 
                        fields="files(id, name, mimeType, thumbnailLink, webViewLink)",
                        pageSize=1000
                    ).execute()
                    folder_inventory_cache[f_id] = res_sub.get('files', [])
                
                target_inventory = folder_inventory_cache[f_id]
            else:
                # Fallback ai file sciolti nella root
                target_inventory = root_inventory
            
            if not target_inventory:
                continue

            # --- LOGICA DI MATCHING ---
            keywords = set()
            if p.brand: keywords.add(p.brand.lower())
            if p.model: keywords.update(p.model.lower().replace('-', ' ').split())
            if p.sku: keywords.add(p.sku.lower())
            
            stop_words = {"pochette", "borsa", "tracolla", "nera", "nero", "pelle", "media", "piccola", "vintage"}
            keywords = {k for k in keywords if len(k) > 3 and k not in stop_words}
            
            if not keywords and not p.sku: continue
            
            valid_images = []
            sku_lower = p.sku.lower() if p.sku else None
            
            for item in target_inventory:
                name_norm = item['name'].lower().replace('_', ' ').replace('-', ' ')
                
                # Check Intersezione
                match_count = sum(1 for k in keywords if k in name_norm)
                
                # Regola: SKU match (Priorità Massima) o Euristica Brand/Keywords MOLTO STRETTA
                is_sku_match = sku_lower and (sku_lower in name_norm)
                if is_sku_match or (p.brand and p.brand.lower() in name_norm and match_count >= 3):
                    valid_images.append({
                        "id": item["id"],
                        "name": item["name"],
                        "thumb": item.get("thumbnailLink"),
                        "link": item.get("webViewLink"),
                        "type": "folder" if "folder" in item["mimeType"] else "image"
                    })
            
            if valid_images:
                # --- ORDINAMENTO NATURALE ---
                # Ordiniamo le foto alfanumericamente (es: 1, 2, 10 invece di 1, 10, 2)
                import re
                def natural_sort_key(s):
                    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
                
                valid_images.sort(key=lambda x: natural_sort_key(x["name"]))

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
                # Non resettiamo drive_folder_id qui per non perdere eventuali info manuali
                
        db.commit()
        add_log(f"✅ [SmartMapper] Sincronizzazione finita: {matched_count} prodotti mappati.")
        return {"status": "ok", "matched": matched_count}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
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

# CONFIG_FILE defined at top

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
    seo_title: Optional[str] = None

class ValidationAction(BaseModel):
    action: str
    data: Optional[ValidationData] = None

@app.get("/atelier-lab")
def atelier_lab(request: Request, category: str = None, brand: str = None, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    # Query più inclusiva per debug e per Postgres
    # Query ultra-permissiva con stringhe dirette per Postgres
    query = db.query(Product).filter(
        (Product.status.in_(["Draft", "Ready", "Validating", "Error", "MATCHED"])) &
        (
            (Product.matched_images_json.isnot(None)) & (Product.matched_images_json != "[]") & (Product.matched_images_json != "") |
            (Product.drive_folder_id.isnot(None)) & (Product.drive_folder_id != "")
        )
    )
    
    if category:
        query = query.filter(Product.tags.like(f"%{category}%"))
    if brand:
        query = query.filter(Product.brand == brand)
        
    products = query.order_by(Product.updated_at.desc()).all()
    
    # Recuperiamo categorie uniche (stratificate dai tag per ora) e brand per i filtri
    brands = db.query(Product.brand).filter(Product.brand != None).distinct().all()
    brands = [b[0] for b in brands]
    
    context = {
        "request": request, 
        "active_page": "atelier_lab", 
        "auth_status": status, 
        "all_systems_go": all_go, 
        "products": products,
        "brands": brands,
        "current_category": category,
        "current_brand": brand
    }
    return templates.TemplateResponse(request=request, name="atelier_lab.html", context=context)

@app.post("/api/lab/certify/{pid}")
async def certify_product(pid: int, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    item = db.query(Product).filter(Product.id == pid).first()
    if not item:
        return {"status": "error", "message": "Prodotto non trovato"}
    
    # Aggiornamento dati manuali dalla Lab
    item.seo_title = payload.get("seo_title", item.seo_title)
    item.tags = payload.get("tags", item.tags)
    item.material = payload.get("material", item.material)
    item.color = payload.get("color", item.color)
    item.dimensions = payload.get("dimensions", item.dimensions)
    item.size = payload.get("size", item.size)
    item.fit = payload.get("fit", item.fit)
    item.condition_grade = payload.get("condition_grade", item.condition_grade)
    item.accessories_included = payload.get("accessories_included", item.accessories_included)
    item.category = payload.get("category", item.category)
    item.ai_description_it = payload.get("ai_description_it", item.ai_description_it)
    
    # Aggiornamento ordine immagini (se fornito dalla UI)
    if "images_order" in payload:
        item.matched_images_json = json.dumps(payload["images_order"])

    
    # Cambio stato intelligente: 
    # Pubblichiamo solo se tutti i campi fondamentali sono presenti, altrimenti resta in lavorazione (Ready)
    if item.seo_title and item.ai_description_it and item.tags:
        item.status = ProductStatus.Published
    else:
        item.status = ProductStatus.Ready
    
    # Recalculate integrity
    from harvester import HarvesterEngine
    item.match_confidence = HarvesterEngine.calculate_integrity(item)
    
    db.commit()
    
    print(f"✅ [Certification] Prodotto {item.sku} certificato e pronto per Shopify.")
    return {"status": "ok"}
    
@app.post("/api/lab/reject/{pid}")
async def reject_product_associations(pid: int, db: Session = Depends(get_db)):
    """Resetta le associazioni immagini di un prodotto e lo riporta in bozza."""
    item = db.query(Product).filter(Product.id == pid).first()
    if not item:
        return {"status": "error", "message": "Prodotto non trovato"}
    
    item.matched_images_json = "[]"
    item.image_match_score = 0.0
    item.status = ProductStatus.Draft
    
    db.commit()
    print(f"🗑️ [Rejection] Associazioni resettate per {item.sku or item.id}. Prodotto riportato in Draft.")
    return {"status": "ok"}

@app.post("/api/lab/research/{pid}")
async def lab_deep_research(pid: int, db: Session = Depends(get_db)):
    """Avvia una ricerca web profonda per arricchire i dati tecnici di un prodotto in Lab."""
    from harvester import HarvesterEngine
    engine = HarvesterEngine()
    result = await engine.deep_research(pid)
    if "error" in result:
        return {"status": "error", "message": result["error"]}
    return {"status": "ok", "data": result}

@app.post("/api/lab/action/{product_id}")
async def process_lab_action(product_id: int, payload: ValidationAction, db: Session = Depends(get_db)):
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
            item.seo_title = payload.data.seo_title
            if payload.data.price:
                try: item.price = float(payload.data.price)
                except ValueError: pass
        
        # TRIGGER AUTO-RENAME SU DRIVE
        if item.drive_folder_id and item.seo_title:
             # Lanciamo in background o attendiamo? Per ora attendiamo per sicurezza
             try:
                 print(f"🚀 [Lab Auto-Rename] Avvio rinomina per {item.sku} -> {item.seo_title}")
                 import asyncio
                 # Nota: vault_seo_rename è una rotta, estraiamo la logica o chiamiamo la funzione interna
                 await perform_drive_rename(item, item.seo_title)
             except Exception as e:
                 print(f"⚠️ [Lab Auto-Rename Warning] Fallito per {item.sku}: {e}")

    elif payload.action == 'left': # Rifiuta
        item.status = ProductStatus.Draft
        
    db.commit()
    return {"status": "ok"}

async def perform_drive_rename(product, new_title):
    creds = google_auth.get_credentials()
    if not creds: return
    from googleapiclient.discovery import build
    drive_service = build('drive', 'v3', credentials=creds)
    
    folder_id = product.drive_folder_id
    safe_name = "".join([c if c.isalnum() or c in " -_" else "" for c in new_title]).strip().upper()
    
    # 1. Rinomina Cartella
    drive_service.files().update(fileId=folder_id, body={'name': safe_name}, supportsAllDrives=True).execute()
    
    # 2. Rinomina File interni
    query = f"'{folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)", supportsAllDrives=True).execute()
    files = results.get('files', [])
    
    idx = 1
    for f in sorted(files, key=lambda x: x['name']):
        if f['mimeType'] == 'application/vnd.google-apps.folder': continue
        ext = f['name'].split('.')[-1] if '.' in f['name'] else 'jpg'
        new_filename = f"{safe_name}-{idx}.{ext}"
        drive_service.files().update(fileId=f['id'], body={'name': new_filename}, supportsAllDrives=True).execute()
        idx += 1


@app.post("/api/lab/regenerate/{product_id}")
async def lab_regenerate(product_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        model_choice = data.get("model", "llama3:latest")
        target = data.get("target", "all")
        
        from ai_agent import AIAgent
        agent = AIAgent()
        
        item = db.query(Product).filter(Product.id == product_id).first()
        if not item: return {"status": "error", "message": "Prodotto non trovato"}
        
        # Sincronizzazione dati dalla UI (per far sì che l'AI legga le tue modifiche a schermo)
        ui_data = data.get("ui_data", {})
        if ui_data:
            if ui_data.get("seo_title"): item.seo_title = ui_data["seo_title"]
            if ui_data.get("category"): item.category = ui_data["category"]
            if ui_data.get("material"): item.material = ui_data["material"]
            if ui_data.get("color"): item.color = ui_data["color"]
            if ui_data.get("dimensions"): item.dimensions = ui_data["dimensions"]
            # Nota: Non facciamo il commit qui, lo facciamo alla fine se la generazione ha successo
            
        # Determiniamo lo stile
        is_regen = bool(item.ai_description_it and len(item.ai_description_it) > 10)
        style_name = "BOUTIQUE (MINIMAL)" if is_regen else "HYBRID (SEO)"
        
        prompt = agent.build_fashion_prompt(item)
        res = await agent.get_clean_json(prompt, model_choice)
        
        response_data = {
            "status": "ok", 
            "engine": f"RE-BOOSTED VIA {model_choice.upper()} | {style_name}"
        }
        
        if target in ['title', 'all']:
            new_title = res.get("seo_title")
            if new_title and len(new_title) > 2:
                item.seo_title = new_title
            response_data["seo_title"] = item.seo_title
            
        if target in ['desc', 'all']:
            new_desc = res.get("ai_description_it")
            if new_desc and len(new_desc) > 10:
                item.ai_description_it = new_desc
            response_data["ai_description_it"] = item.ai_description_it
            
        if target in ['tags', 'all']:
            raw_tags = res.get("tags", [])
            if raw_tags:
                from harvester import HarvesterEngine
                engine = HarvesterEngine()
                cleaned_tags = engine._clean_tags(raw_tags)
                item.tags = ", ".join(cleaned_tags)
            response_data["tags"] = item.tags

        # Aggiorniamo la dicitura della sorgente per veridicità
        sandbox = {}
        if item.raw_harvested_data:
            try: 
                sandbox = json.loads(item.raw_harvested_data)
            except: 
                pass
        sandbox["source_engine"] = response_data["engine"]
        item.raw_harvested_data = json.dumps(sandbox)

        db.commit()
        return response_data
    except Exception as e:
        import traceback
        print(f"❌ Errore Lab Regenerate: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}

@app.post("/api/lab/generate-seo/{product_id}")
async def lab_generate_seo(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    model_choice = data.get("model", "llama3:latest")
    from ai_agent import AIAgent
    agent = AIAgent()
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    prompt = agent.build_fashion_prompt(item)
    res = await agent.get_clean_json(prompt, model_choice)
    
    seo_title = res.get("seo_title")
    engine_name = f"RE-BOOSTED VIA {model_choice.upper()}"
    
    if seo_title:
        item.seo_title = seo_title
        
        # Aggiorniamo la sorgente
        sandbox = {}
        if item.raw_harvested_data:
            try: sandbox = json.loads(item.raw_harvested_data)
            except: pass
        sandbox["source_engine"] = engine_name
        item.raw_harvested_data = json.dumps(sandbox)
        
        db.commit()
    
    return {"status": "ok", "seo_title": item.seo_title, "engine": engine_name}

@app.get("/api/system/usage")
async def system_usage(db: Session = Depends(get_db)):
    usage = db.query(ApiUsage).filter(ApiUsage.service_name == "serper").first()
    return {"serper": usage.total_hits if usage else 0}

@app.post("/api/lab/generate-tags/{product_id}")
async def lab_generate_tags(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    model_choice = data.get("model", "llama3:latest")
    from ai_agent import AIAgent
    agent = AIAgent()
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    prompt = agent.build_fashion_prompt(item)
    res = await agent.get_clean_json(prompt, model_choice)
    
    raw_tags = res.get("tags", [])
    if raw_tags:
        from harvester import HarvesterEngine
        engine = HarvesterEngine()
        cleaned_tags = engine._clean_tags(raw_tags)
        item.tags = ", ".join(cleaned_tags)
        db.commit()
        db.refresh(item)
    
    return {"status": "ok", "tags": item.tags}

@app.post("/api/lab/generate-seo/{product_id}")
async def lab_generate_seo(product_id: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    model_choice = data.get("model", "llama3")
    from ai_agent import AIAgent
    agent = AIAgent()
    
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    prompt = agent.build_fashion_prompt(item)
    res = await agent.get_clean_json(prompt, model_choice)
    
    seo_title = res.get("seo_title")
    if seo_title:
        item.seo_title = seo_title
        
        # Aggiorniamo anche la dicitura dell'engine per trasparenza (veridicità)
        sandbox = {}
        if item.raw_harvested_data:
            try: sandbox = json.loads(item.raw_harvested_data)
            except: pass
        
        sandbox["source_engine"] = f"RE-BOOSTED VIA {model_choice.upper()}"
        item.raw_harvested_data = json.dumps(sandbox)
        
        db.commit()
        db.refresh(item)
    
    return {"status": "ok", "seo_title": item.seo_title, "engine": f"RE-BOOSTED VIA {model_choice.upper()}"}

@app.get("/api/shopify/status")
async def shopify_status():
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    ok, message = await bridge.check_connection()
    return {"connected": ok, "message": message}

@app.get("/api/system/health")
async def system_health(db: Session = Depends(get_db)):
    import auth_manager
    import httpx
    from shopify_bridge import add_bridge_log
    
    # 1. Database Check
    try:
        db.execute(text("SELECT 1"))
        db_status = {"ok": True, "val": "Connesso (SQLite)"}
    except Exception as e:
        db_status = {"ok": False, "val": "Errore DB"}

    # 2. Drive Check
    drive_ok = auth_manager.check_google_auth()
    drive_status = {"ok": drive_ok, "val": "Sessione Attiva" if drive_ok else "Scollegato"}

    # 3. AI Check (Ollama)
    ai_status = {"ok": False, "val": "Ollama Offline"}
    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ai_status = {"ok": True, "val": "Ollama Attivo"}
    except:
        pass

    add_bridge_log(f"🩺 Health Check: DB={'✅' if db_status['ok'] else '❌'}, Drive={'✅' if drive_status['ok'] else '❌'}, AI={'✅' if ai_status['ok'] else '❌'}")

    return {
        "db": db_status,
        "drive": drive_status,
        "ai": ai_status
    }

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
        f"RUOLO: {system_role}\n"
        f"TONO: {selected_tone_instr}\n"
        f"PRODOTTO: {item.brand} {item.model}\n"
        f"DETTAGLI TECNICI: Materiale {item.material or 'N/D'}, Colore {item.color or 'N/D'}, Hardware {item.hardware_type or 'N/D'}, Condizioni {item.condition_grade or 'N/D'}.\n"
    )
    
    if instructions:
        prompt += f"NOTE AGGIUNTIVE: {instructions}\n"
        
    # Logica Dinamica: Se esiste già una descrizione, passiamo allo STILE 3 (Boutique)
    is_regen = bool(item.ai_description_it and len(item.ai_description_it) > 10)
    
    style_rules = ""
    if is_regen:
        style_rules = (
            "1. **OPZIONE 3 - BOUTIQUE**: 1-2 frasi di altissimo impatto (lusso estremo).\n"
            "2. **ELENCO PUNTATO**: Usa i label 'Stato' e 'Corredo'. Ogni punto su una riga.\n"
        )
    else:
        style_rules = (
            "1. **OPZIONE 2 - IBRIDA**: Gancio emozionale (2-3 righe) + Elenco tecnico.\n"
            "2. **ELENCO PUNTATO**: Usa 'Materiale', 'Dettagli', 'Condizioni', 'Corredo'. Ogni punto su una riga.\n"
        )

    prompt += (
        f"\nREGOLE MANDATORIE DI STRUTTURA E FORMATTAZIONE:\n"
        f"{style_rules}"
        "3. **RIGA VUOTA**: Inserisci una riga vuota tra il paragrafo e l'elenco puntato.\n"
        "4. **VOCABOLARIO**: Mai usare 'sneakers' (usa 'scarpe'), 'comfort' è MASCHILE, 'scarpe' è FEMMINILE PLURALE.\n"
        "5. **CHIUSURA**: Una riga finale elegante.\n"
        "\nComponi ora la descrizione in italiano:"
    )

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


from typing import Optional

@app.post("/api/settings/keys")
def save_keys(
    request: Request, 
    openai: Optional[str] = Form(None),
    anthropic: Optional[str] = Form(None),
    gemini: Optional[str] = Form(None),
    serper: Optional[str] = Form(None),
    shopify_token: Optional[str] = Form(None),
    shopify_url: Optional[str] = Form(None),
    shopify_client_id: Optional[str] = Form(None),
    shopify_client_secret: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # Pulizia Shopify URL (rimozione https:// e slash finali)
    if shopify_url:
        shopify_url = shopify_url.replace("https://", "").replace("http://", "").split("/")[0].strip()
    
    inputs = {
        "openai": openai,
        "anthropic": anthropic,
        "gemini": gemini,
        "serper": serper,
        "shopify_token": shopify_token,
        "shopify_url": shopify_url,
        "shopify_client_id": shopify_client_id,
        "shopify_client_secret": shopify_client_secret
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


@app.get("/api/settings/test-key")
async def test_api_key(service: str):
    """Verifica se una chiave API è valida facendo una piccola richiesta di test."""
    key = auth_manager.get_raw_api_key(service)
    if not key:
        return {"status": "error", "message": "Chiave non trovata."}
    
    try:
        async with httpx.AsyncClient() as client:
            if service == "openai":
                resp = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
                if resp.status_code == 200: return {"status": "ok"}
                return {"status": "error", "message": "Chiave OpenAI non valida o scaduta."}
            
            elif service == "gemini":
                # Usiamo la lista modelli come ping veloce
                resp = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
                if resp.status_code == 200: return {"status": "ok"}
                return {"status": "error", "message": "Chiave Gemini non valida."}
                
            elif service == "serper":
                resp = await client.post("https://google.serper.dev/search", 
                                        headers={"X-API-KEY": key, "Content-Type": "application/json"},
                                        json={"q": "apple"})
                if resp.status_code == 200: return {"status": "ok"}
                return {"status": "error", "message": "Chiave Serper non valida."}
            
            return {"status": "error", "message": "Servizio non supportato per il test."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


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

@app.post("/api/darkroom/convert-bulk")
async def drive_convert_bulk(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    file_ids = data.get("file_ids", [])
    # Avviamo il lavoro pesante in background
    background_tasks.add_task(process_conversions, file_ids)
    return {"status": "finished", "message": "Sviluppo avviato in background."}

CONVERSION_STATUS = {"total": 0, "current": 0, "active": False, "stage": "Inattivo", "results": []}

@app.get("/api/darkroom/conversion-status")
def get_conversion_status():
    return CONVERSION_STATUS

def process_conversions(file_ids):
    global CONVERSION_STATUS
    CONVERSION_STATUS["active"] = True
    CONVERSION_STATUS["total"] = len(file_ids)
    CONVERSION_STATUS["current"] = 0
    CONVERSION_STATUS["stage"] = "Avvio sessione..."
    CONVERSION_STATUS["results"] = []
    
    import google_auth
    try:
        creds = google_auth.get_credentials()
        if not creds:
            CONVERSION_STATUS["stage"] = "Errore: Credenziali mancanti"
            CONVERSION_STATUS["active"] = False
            return
            
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Carico cartella di destinazione
        cnf = {}
        if os.path.exists("workspace_config.json"):
            with open("workspace_config.json", 'r') as f:
                cnf = json.load(f)
        target_root_id = cnf.get("folder_out_id")

        for idx, file_id in enumerate(file_ids):
            try:
                current_step = f"({idx+1}/{len(file_ids)})"
                CONVERSION_STATUS["stage"] = f"{current_step} Analisi percorso..."
                
                meta = drive_service.files().get(fileId=file_id, fields="id, name, parents", supportsAllDrives=True).execute()
                parents = meta.get('parents', [])
                source_parent_id = parents[0] if parents else None
                
                # --- LOGICA MIRROR PROFONDO ---
                source_root_id = cnf.get("folder_id")
                target_folder_id = target_root_id or source_parent_id
                
                if target_root_id and source_parent_id and source_root_id:
                    # Ricostruiamo il percorso dal parent fino alla root
                    path_folders = []
                    curr_id = source_parent_id
                    
                    while curr_id and curr_id != source_root_id:
                        f_meta = drive_service.files().get(fileId=curr_id, fields="id, name, parents", supportsAllDrives=True).execute()
                        path_folders.append(f_meta.get("name"))
                        p = f_meta.get("parents", [])
                        curr_id = p[0] if p else None
                        if len(path_folders) > 10: break # Limite di sicurezza
                    
                    # Invertiamo per avere l'ordine Root -> File
                    path_folders.reverse()
                    
                    # Ricreiamo il percorso nel target
                    last_target_id = target_root_id
                    for f_name in path_folders:
                        q = f"name = '{f_name}' and '{last_target_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                        res_f = drive_service.files().list(q=q, fields="files(id)", supportsAllDrives=True).execute()
                        found = res_f.get("files", [])
                        
                        if found:
                            last_target_id = found[0]['id']
                        else:
                            new_f = drive_service.files().create(body={
                                'name': f_name,
                                'mimeType': 'application/vnd.google-apps.folder',
                                'parents': [last_target_id]
                            }, fields='id', supportsAllDrives=True).execute()
                            last_target_id = new_f['id']
                    
                    target_folder_id = last_target_id

                CONVERSION_STATUS["stage"] = f"{current_step} Sviluppo: {meta['name']}"
                raw_data = drive_service.files().get_media(fileId=file_id).execute()
                img = Image.open(io.BytesIO(raw_data))
                
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=95, optimize=True)
                out.seek(0)
                
                media = MediaIoBaseUpload(out, mimetype='image/jpeg')
                new_name = meta['name'].rsplit('.', 1)[0] + ".jpg"
                
                CONVERSION_STATUS["stage"] = f"{current_step} Salvataggio JPEG: {new_name}"
                new_jpg = drive_service.files().create(body={
                    'name': new_name, 
                    'parents': [target_folder_id]
                }, media_body=media, fields='id', supportsAllDrives=True).execute()
                
                # Salviamo il nuovo ID per il passaggio automatico al matching
                CONVERSION_STATUS["results"].append(new_jpg['id'])
                
                time.sleep(0.3)
                
            except Exception as e:
                err_msg = str(e)
                print(f"❌ Errore file {file_id}: {err_msg}")
                CONVERSION_STATUS["stage"] = f"Errore su {file_id}: {err_msg.split(':')[0]}"
                time.sleep(2)
            
            CONVERSION_STATUS["current"] += 1
            
    except Exception as e:
        CONVERSION_STATUS["stage"] = f"Errore Fatale: {str(e)}"
        
    CONVERSION_STATUS["active"] = False
    if "Errore" not in CONVERSION_STATUS["stage"]:
        CONVERSION_STATUS["stage"] = "Completato"

@app.get("/api/drive/proxy/{file_id}")
async def drive_proxy(file_id: str):
    # 1. Check Cache
    link = THUMBNAIL_CACHE.get(file_id)
    
    if not link:
        # 2. Se non in cache, recupero veloce (SDK in thread)
        drive_service = get_drive_service()
        if drive_service:
            try:
                loop = asyncio.get_event_loop()
                meta = await loop.run_in_executor(None, lambda: drive_service.files().get(
                    fileId=file_id, fields="thumbnailLink", supportsAllDrives=True
                ).execute())
                link = meta.get("thumbnailLink")
                if link: THUMBNAIL_CACHE[file_id] = link
            except: pass
            
    # 3. Download Thumbnail (Async)
    if link:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(link.replace("=s220", "=s800"))
                if resp.status_code == 200:
                    return Response(
                        content=resp.content, 
                        media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"}
                    )
        except: pass

    # 4. Fallback: Download Integrale (Molto lento, in thread)
    try:
        drive_service = get_drive_service()
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: drive_service.files().get_media(fileId=file_id).execute())
        return StreamingResponse(io.BytesIO(res), media_type="image/jpeg")
    except Exception as e:
        return StreamingResponse(io.BytesIO(b""), media_type="image/png")

@app.post("/api/darkroom/files-by-ids")
async def get_files_by_ids(request: Request):
    data = await request.json()
    ids = data.get("ids", [])
    if not ids: return []
    
    drive_service = get_drive_service()
    if not drive_service: return {"error": "non_auth"}
    
    results = []
    for fid in ids:
        try:
            f = drive_service.files().get(fileId=fid, fields="id, name, mimeType, size, thumbnailLink, createdTime", supportsAllDrives=True).execute()
            results.append(f)
        except: continue
    return results

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

@app.get("/api/product/{pid}")
def get_product_data(pid: int, db: Session = Depends(get_db)):
    from logger_utils import add_log
    try:
        item = db.query(Product).filter(Product.id == pid).first()
        if not item: return {"status": "error", "message": "Prodotto non trovato"}
        return {
            "id": item.id,
            "brand": item.brand or "",
            "model": item.model or "",
            "sku": item.sku or "",
            "price": item.price or 0.0,
            "tags": item.tags or "",
            "seo_title": item.seo_title or "",
            "category": item.category or "",
            "ai_description_it": item.ai_description_it or "",
            "governance_category_id": item.governance_category_id,
            "size": item.size or "",
            "fit": item.fit or "",
            "condition_grade": item.condition_grade or "",
            "accessories_included": item.accessories_included or "",
            "dimensions": item.dimensions or "",
            "raw_harvested_data": item.raw_harvested_data or ""
        }
    except Exception as e:
        add_log(f"💥 [API Error] Recupero Prodotto #{pid}: {str(e)}")
        return {"status": "error", "message": str(e)}

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
    
    # Recuperiamo anche le categorie di governance per la board
    gov_categories = db.query(CategoryGovernance).all()
    categories_data = []
    for gc in gov_categories:
        rules = db.query(CategoryRule).filter(CategoryRule.category_id == gc.id).all()
        categories_data.append({
            "id": gc.id,
            "name": gc.name,
            "shopify_id": gc.shopify_collection_id,
            "disjunctive": gc.applied_disjunctively,
            "rules_count": len(rules),
            "policy_tags": gc.required_tags,
            "forbidden_tags": gc.forbidden_tags
        })

    context = {
        "request": request, 
        "active_page": "tag_central", 
        "auth_status": status, 
        "all_systems_go": all_go, 
        "tags": sorted_tags,
        "governance_categories": categories_data
    }
    return templates.TemplateResponse(request=request, name="tag_central.html", context=context)

@app.post("/api/tags/manage")
async def manage_tag(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    tag = data.get("tag")
    action = data.get("action") 
    new_name = data.get("new_name")
    
    if not tag or not action:
        return {"status": "error", "message": "Dati mancanti"}
    
    # Operiamo solo a livello di prodotti per questo tag
    products = db.query(Product).filter(Product.tags.contains(tag)).all()
    
    if action == "blacklist" or action == "delete":
        for p in products:
            p.tags = ", ".join([t.strip() for t in (p.tags or "").split(",") if t.strip() and t.strip().lower() != tag.lower()])
            
    elif action == "rename" and new_name:
        for p in products:
            tags = [t.strip() for t in (p.tags or "").split(",") if t.strip()]
            new_tags = [new_name if t.lower() == tag.lower() else t for t in tags]
            p.tags = ", ".join(list(set(new_tags)))
            
    db.commit()
    return {"status": "ok"}

# --- GOVERNANCE API ---

@app.post("/api/governance/sync")
async def sync_governance(db: Session = Depends(get_db)):
    from shopify_bridge import ShopifyBridge
    bridge = ShopifyBridge()
    count = await bridge.sync_governance_categories(db)
    return {"status": "ok", "synced": count}

@app.get("/api/governance/categories")
def get_gov_categories(db: Session = Depends(get_db)):
    cats = db.query(CategoryGovernance).all()
    return cats

@app.post("/api/governance/audit")
async def run_governance_audit():
    engine = GovernanceEngine()
    issues = await engine.audit_all_products()
    return {"status": "ok", "issues": issues}

@app.post("/api/governance/save-policy")
async def save_governance_policy(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    cat_id = data.get("category_id")
    req_tags = data.get("required_tags")
    forb_tags = data.get("forbidden_tags")
    
    cat = db.query(CategoryGovernance).filter(CategoryGovernance.id == cat_id).first()
    if not cat: return {"status": "error", "message": "Categoria non trovata"}
    
    if req_tags is not None: cat.required_tags = req_tags
    if forb_tags is not None: cat.forbidden_tags = forb_tags
    
    db.commit()
    return {"status": "ok"}

@app.post("/api/governance/apply-policy/{cid}")
async def apply_policy_to_category(cid: int, db: Session = Depends(get_db)):
    # Trova tutti i prodotti in questa categoria
    products = db.query(Product).filter(Product.governance_category_id == cid).all()
    engine = GovernanceEngine()
    
    count = 0
    for p in products:
        success = await engine.apply_policy_tags(p.id)
        if success: count += 1
        
    return {"status": "ok", "applied_to": count}

@app.post("/api/product/reclassify/{pid}")
async def reclassify_product(pid: int, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    cat_id = data.get("category_id")
    
    item = db.query(Product).filter(Product.id == pid).first()
    if not item: return {"status": "error", "message": "Prodotto non trovato"}
    
    item.governance_category_id = cat_id
    db.commit()
    
    # Opzionale: applica tag della nuova categoria
    engine = GovernanceEngine()
    await engine.apply_policy_tags(pid)
    
    return {"status": "ok", "message": "Prodotto riclassificato."}

@app.post("/api/product/delete-bulk")
async def delete_products_bulk(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    ids = data.get("ids", [])
    if not ids: return {"status": "error"}
    
    db.query(Product).filter(Product.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"status": "ok", "deleted": len(ids)}

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
