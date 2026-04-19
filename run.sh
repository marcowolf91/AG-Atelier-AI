#!/bin/bash
echo "Attivando Virtual Environment..."
source venv/bin/activate

echo "Avvio Engine Atelier AI sulla porta 8002..."
uvicorn app:app --reload --port 8002
