#!/bin/bash
echo "Attivando Virtual Environment..."
source venv/bin/activate

echo "Avvio Engine Atelier AI sulla porta 8003..."
source venv/bin/activate
uvicorn app:app --reload --port 8003 --host 127.0.0.1
