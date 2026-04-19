from fastapi import FastAPI, Depends, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db, Product, ProductStatus, Setting
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
    total_products = db.query(Product).count()
    validating = db.query(Product).filter(Product.status == ProductStatus.Validating).count()
    published = db.query(Product).filter(Product.status == ProductStatus.Published).count()
    errors = db.query(Product).filter(Product.status == ProductStatus.Error).count()
    
    # Calculate costs
    from sqlalchemy.sql import func
    total_cost_calc = db.query(func.sum(Product.api_cost_usd)).scalar() or 0.0

    status, all_go = get_system_status()

    context = {
        "request": request,
        "kpi_total": total_products,
        "kpi_validating": validating,
        "kpi_published": published,
        "kpi_errors": errors,
        "kpi_cost": round(total_cost_calc, 4),
        "active_page": "dashboard",
        "auth_status": status,
        "all_systems_go": all_go,
        "recent_products": db.query(Product).order_by(Product.id.desc()).limit(15).all()
    }

    return templates.TemplateResponse("dashboard.html", context)

@app.get("/the-loom")
def the_loom(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    # Mostriamo solo drafts o error
    products = db.query(Product).filter(Product.status.in_([ProductStatus.Draft, ProductStatus.Error])).limit(50).all()
    context = {"request": request, "active_page": "the_loom", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse("the_loom.html", context)


@app.get("/the-harvester")
def the_harvester(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "the_harvester", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse("the_harvester.html", context)

@app.get("/the-muse")
def the_muse(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    # Mostriamo solo i products in Processing per cui la ricerca è finita
    products = db.query(Product).filter(Product.status == ProductStatus.Processing).all()
    context = {"request": request, "active_page": "the_muse", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse("the_muse.html", context)

@app.get("/shopify-cloud")
def shopify_cloud(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "shopify_cloud", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse("shopify_cloud.html", context)

@app.get("/catalog")
def open_catalog(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    products = db.query(Product).filter(Product.status.in_([ProductStatus.Published, ProductStatus.Ready])).all()
    context = {"request": request, "active_page": "catalog", "auth_status": status, "all_systems_go": all_go, "products": products}
    return templates.TemplateResponse("catalog.html", context)

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
    """Avvia la sync in background via task queue, oppure con await per semplificare"""
    from sync_engine import engine
    
    # Aspettiamo il check fake asincrono
    imported = await engine.sync_sheets("dummy_sheet_id", db)
    
    # Re-inviamo alla dashboard
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/harvester/start")
async def start_harvester(background_tasks: BackgroundTasks):
    import harvester
    engine = harvester.HarvesterEngine()
    background_tasks.add_task(engine.run_harvester)
    return {"status": "started"}

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
    return templates.TemplateResponse("engine_room.html", context)

@app.get("/auth/google/login")
def google_login(request: Request):
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/auth/google/callback"
    url, err = google_auth.get_google_auth_url(redirect_uri)
    if not url:
        return RedirectResponse(url="/engine-room?error=secrets_missing", status_code=303)
    return RedirectResponse(url=url)

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
    return templates.TemplateResponse("atelier_lab.html", context)

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
class MuseSaveData(BaseModel):
    description: str
    api_cost: Optional[float] = 0.001 # Costo stimato mock

@app.post("/api/muse/save/{product_id}")
def save_muse_description(product_id: int, payload: MuseSaveData, db: Session = Depends(get_db)):
    item = db.query(Product).filter(Product.id == product_id).first()
    if not item:
        return {"error": "Not found"}
        
    item.description = payload.description
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
    return templates.TemplateResponse(f"{page_name}.html", context)

@app.get("/system-logs")
def system_logs(request: Request, db: Session = Depends(get_db)):
    status, all_go = get_system_status()
    context = {"request": request, "active_page": "system_logs", "auth_status": status, "all_systems_go": all_go}
    return templates.TemplateResponse("system_logs.html", context)

@app.get("/loom-mapping")
def loom_mapping(request: Request, db: Session = Depends(get_db)): return render_placeholder("loom_mapping", request, db)

@app.get("/prompt-vault")
def prompt_vault(request: Request, db: Session = Depends(get_db)): return render_placeholder("prompt_vault", request, db)

@app.get("/media-vault")
def media_vault(request: Request, db: Session = Depends(get_db)): return render_placeholder("media_vault", request, db)

@app.get("/finance")
def finance(request: Request, db: Session = Depends(get_db)): return render_placeholder("finance", request, db)

@app.get("/shopify-routing")
def shopify_routing(request: Request, db: Session = Depends(get_db)): return render_placeholder("shopify_routing", request, db)
