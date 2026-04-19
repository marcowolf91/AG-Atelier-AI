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
    sku = Column(String, unique=True, index=True)
    brand = Column(String, index=True)
    model = Column(String)
    category = Column(String)
    
    status = Column(SQLEnum(ProductStatus), default=ProductStatus.Draft)
    
    condition_grade = Column(String) # e.g. Excellent, Pristine, Good
    material = Column(String)
    color = Column(String)
    hardware_type = Column(String) # e.g. Gold, Silver
    price = Column(Float, nullable=True)
    
    dimensions = Column(String, nullable=True)
    source_urls = Column(Text, nullable=True) # JSON or Comma-separated list
    discrepancy_flag = Column(String, nullable=True)
    
    original_sheets_row = Column(Integer, nullable=True)
    drive_folder_url = Column(String, nullable=True)
    ai_description_it = Column(Text, nullable=True)
    tags = Column(String, nullable=True) # JSON or CSV array of tags
    image_match_score = Column(Float, nullable=True) # Percentuale di similarity
    api_cost_usd = Column(Float, default=0.0) # Tracciamento costi AI/Scraping

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
