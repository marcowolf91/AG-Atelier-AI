import enum
import datetime
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Enum as SQLEnum, Text, Float, DateTime, func
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Priorità alla DATABASE_URL di Supabase, fallback su SQLite locale
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./atelier_ai.db")

# Per Postgres (Supabase) dobbiamo assicurarci che la stringa inizi con postgresql://
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine_args = {"connect_args": {"check_same_thread": False}}

engine = create_engine(SQLALCHEMY_DATABASE_URL, **engine_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class ProductStatus(enum.Enum):
    Draft = "Draft"
    Processing = "Processing"
    Validating = "Validating"
    Ready = "Ready"
    Published = "Published"
    Error = "Error"

class CategoryGovernance(Base):
    __tablename__ = "category_governance"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    shopify_collection_id = Column(String, nullable=True, unique=True)
    description = Column(Text, nullable=True)
    applied_disjunctively = Column(Integer, default=0) # 0 = ALL rules (AND), 1 = ANY rule (OR)
    required_tags = Column(Text, nullable=True) # Policy: tags that should be auto-applied

class CategoryRule(Base):
    __tablename__ = "category_rules"
    
    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, index=True)
    column = Column(String) # e.g. TAG, VENDOR, PRODUCT_TYPE
    relation = Column(String) # e.g. EQUALS, CONTAINS
    condition = Column(String) # Value to match

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    sku = Column(String, nullable=True, index=True)
    brand = Column(String, index=True)
    model = Column(String)
    category = Column(String)
    description = Column(Text, nullable=True)
    
    status = Column(SQLEnum(ProductStatus), default=ProductStatus.Draft)
    
    condition_grade = Column(String) # e.g. Excellent, Pristine, Good
    material = Column(String)
    color = Column(String)
    hardware_type = Column(String) # e.g. Gold, Silver
    price = Column(Float, nullable=True)
    cost_price = Column(Float, nullable=True) # Sede Costo
    
    dimensions = Column(String, nullable=True)
    size = Column(String, nullable=True) # Taglia Scarpe / Misura
    fit = Column(String, nullable=True) # Calzata / Vestibilità
    handle_drop = Column(String, nullable=True) # Luce Manici/Tracolla
    accessories_included = Column(String, nullable=True) # Corredo Incluso
    location = Column(String, nullable=True) # Sede
    
    original_sheets_row = Column(Integer, nullable=True)
    master_sync_status = Column(String, default="synced") # "synced" or "orphaned"
    source_sheet = Column(String, nullable=True) # e.g. "Borse Donna"
    drive_folder_url = Column(String, nullable=True)
    drive_folder_id = Column(String, nullable=True)
    ai_description_it = Column(Text, nullable=True)
    raw_harvested_data = Column(Text, nullable=True)
    seo_title = Column(String, nullable=True)
    tags = Column(Text, nullable=True) # Tag separati da virgola
    matched_images_json = Column(Text, nullable=True) # List of image IDs or URLs from Drive
    image_match_score = Column(Float, nullable=True)
    match_confidence = Column(Float, nullable=True) # Percentuale di affidabilità del match (0-100)
    api_cost_usd = Column(Float, default=0.0)
    
    # Sync & Conflict Tracking
    has_master_conflict = Column(Integer, default=0) # 0=Synced, 1=Conflict detected
    master_snapshot_json = Column(Text, nullable=True) # Last known clean values from Sheet
    
    # Governance Link
    governance_category_id = Column(Integer, nullable=True, index=True)

    # Campi per Triage e Prevenzione Looping
    is_ai_processing = Column(Integer, default=0) # 1 se un job in background è attivo
    last_ai_error = Column(Text, nullable=True) # Per mostrare cosa è andato storto all'utente

class HarvesterSetting(Base):
    __tablename__ = "harvester_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    depth = Column(String, default="Standard") # Standard, Deep
    include_official_sites = Column(Integer, default=1) # 1 = Yes, 0 = No
    auto_tagging = Column(Integer, default=1)

class GlobalTagGovernance(Base):
    __tablename__ = "global_tag_governance"
    
    id = Column(Integer, primary_key=True, index=True)
    tag_name = Column(String, unique=True, index=True)
    status = Column(String, default="Standard") # Standard, Blacklisted, Certified
    replacement_tag = Column(String, nullable=True) # Per auto-rename

class ApiUsage(Base):
    __tablename__ = "api_usage"
    
    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, unique=True, index=True) # e.g. "serper", "openai"
    total_hits = Column(Integer, default=0)
    last_used = Column(DateTime, default=datetime.datetime.utcnow)

class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, unique=True, index=True) 
    is_connected = Column(Integer, default=0) # 0 = false, 1 = true
    last_checked = Column(String, nullable=True)

# Crea le tabelle nel DB (se non esistono)
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
