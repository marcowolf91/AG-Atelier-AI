
import sys
import os
from sqlalchemy.orm import Session
from database import SessionLocal, Setting
import auth_manager

def test_save_flow():
    db = SessionLocal()
    try:
        service = "gemini"
        key = "AIza_test_key_123"
        
        print(f"Testing save_api_key for {service}...")
        auth_manager.save_api_key(service, key)
        
        print(f"Testing DB query for {service}...")
        setting = db.query(Setting).filter(Setting.service_name == service).first()
        if not setting:
            print(f"Creating new setting for {service}...")
            setting = Setting(service_name=service, is_connected=1)
            db.add(setting)
        else:
            print(f"Updating existing setting for {service}...")
            setting.is_connected = 1
            
        print("Committing to DB...")
        db.commit()
        print("Success!")
    except Exception as e:
        print(f"FAILURE: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_save_flow()
