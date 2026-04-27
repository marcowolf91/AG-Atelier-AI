import datetime

LIVE_LOGS = []

def add_log(message: str):
    # Formattazione narrativa con icone basata sul testo
    if "Harvester" in message or "Engine" in message: emoji = "🚜"
    elif "AI" in message or "Ollama" in message: emoji = "🤖"
    elif "Drive" in message: emoji = "📸"
    elif "Error" in message or "Errore" in message: emoji = "⚠️"
    else: emoji = "✨"
    
    clean_msg = f"{emoji} {message}"
    LIVE_LOGS.append(clean_msg)
    if len(LIVE_LOGS) > 50:
        LIVE_LOGS.pop(0)
    with open("harvester_debug.log", "a") as f:
        f.write(f"{datetime.datetime.now()} - {clean_msg}\n")
        f.flush()
        import os
        try:
            os.fsync(f.fileno())
        except: pass
