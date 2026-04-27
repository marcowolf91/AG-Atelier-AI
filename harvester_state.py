# Stato globale condiviso per evitare disallineamenti tra thread
ENGINE_STATE = {
    "status": "IDLE",
    "current_batch_ids": [],
    "pending_ids": [],
    "processed_count": 0,
    "batch_size": 5
}

PROCESS_PROGRESS = {
    "total": 0,
    "completed": 0
}
