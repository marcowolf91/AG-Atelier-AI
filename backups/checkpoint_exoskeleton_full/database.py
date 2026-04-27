import enum
from sqlalchemy import create_engine, Column, Integer, String, Enum as SQLEnum, Text, Float
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./atelier_ai.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class ProductStatus(enum.Enum):
    Draft = "Draft"
    Processing = "Processing"
    Validating = "Validating"
    Ready = "Ready"
    Published = "Published"
    Error = "Error"

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
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
    api_cost_usd = Column(Float, default=0.0)

    # Campi per Triage e Prevenzione Looping
    is_ai_processing = Column(Integer, default=0) # 1 se un job in background è attivo
    last_ai_error = Column(Text, nullable=True) # Per mostrare cosa è andato storto all'utente

class HarvesterSetting(Base):
    __tablename__ = "harvester_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    depth = Column(String, default="Standard") # Standard, Deep
    include_official_sites = Column(Integer, default=1) # 1 = Yes, 0 = No
    auto_tagging = Column(Integer, default=1)

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
