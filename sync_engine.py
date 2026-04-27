import os
import json
import logging
from database import SessionLocal, Product, ProductStatus

# Stato globale per la UI
SYNC_STATUS = {
    "total_sheets": 0,
    "current_sheet_idx": 0,
    "current_sheet_name": "",
    "is_running": False,
    "last_imported": 0,
    "last_updated": 0
}

class SyncEngine:
    def __init__(self):
        self.mapping = {
            "brand": ["brand", "marchio", "marca", "vendor", "produttore"],
            "model": ["model", "modello", "nome articolo", "articolo", "titolo"],
            "description": ["description", "descrizione", "desc", "descr", "presentazione"],
            "material": ["material", "materiale", "pelle", "tessuto", "leather", "canvas"],
            "color": ["color", "colore", "nuance", "tinta"],
            "condition": ["condition", "condizioni", "condiz", "grado", "rank"],
            "price": ["price", "prezzo", "amount", "valore", "listing price", "costo", "vendita", "sell price", "listino"],
            "size": ["size", "taglia", "misura", "numero", "lunghezza", "width"],
            "serial": ["serial", "seriale", "barcode", "sku", "codice", "barcode", "upc", "ean"]
        }

    def normalize_field(self, val, field_type=None):
        if val is None: return ""
        s = str(val).strip()
        
        # Brand Typos Fix
        if field_type == "brand":
            s_up = s.upper()
            brand_fixes = {
                "BALENCIGA": "BALENCIAGA",
                "LOUSI VUITTON": "LOUIS VUITTON",
                "VELENTINO": "VALENTINO",
            }
            return brand_fixes.get(s_up, s_up)
        if field_type == "color":
            s_up = s.upper().strip()
            color_map = {
                "BLUE": "BLU",
                "BLACK": "NERO",
                "WHITE": "BIANCO",
                "RED": "ROSSO",
                "GREEN": "VERDE",
                "YELLOW": "GIALLO",
                "GREY": "GRIGIO",
                "GRAY": "GRIGIO",
                "BROWN": "MARRONE",
                "PINK": "ROSA",
                "ORANGE": "ARANCIONE",
                "PURPLE": "VIOLA",
                "GOLD": "ORO",
                "SILVER": "ARGENTO",
                "BEIGE": "BEIGE",
                "CUIR": "CUOIO",
                "EBENE": "EBANO",
                "AZURE": "AZZURRO",
                "NAVY": "BLU NOTTE",
                "BORDEAUX": "BORDEAUX",
                "BURGUNDY": "BORDEAUX"
            }
            # Se il valore esatto è nella mappa, lo sostituiamo
            if s_up in color_map:
                return color_map[s_up]
            
            # Altrimenti facciamo pulizia parziale per stringhe composte (es. "Light Blue")
            for eng, ita in color_map.items():
                if eng in s_up:
                    return ita
            
            return s_up
        if field_type == "material":
            s_up = s.upper().strip()
            mat_map = {
                "CUIR": "PELLE",
                "LEATHER": "PELLE",
                "CANVAS": "TELA",
                "SILK": "SETA",
                "WOOL": "LANA",
                "COTTON": "COTONE",
                "PVC": "PVC",
                "RUBBER": "GOMMA"
            }
            if s_up in mat_map:
                return mat_map[s_up]
            for eng, ita in mat_map.items():
                if eng in s_up:
                    return ita
            return s_up
        return s

    async def sync_sheets(self, sheet_id, db_session, range_name="A:Z"):
        """Sincronizzazione avanzata con discovery automatico della riga di intestazione."""
        import google_auth
        from googleapiclient.discovery import build
        
        # Estrazione nome foglio robusta
        target_sheet_name = range_name
        if "!" in range_name:
            target_sheet_name = range_name.split("!")[0].strip("'").strip()
        else:
            # Se non c'è !, forse è proprio solo il nome del foglio
            target_sheet_name = range_name.strip("'").strip()
            # In questo caso espandiamo il range per l'API di Google
            range_name = f"'{target_sheet_name}'!A:Z"
        
        if not target_sheet_name or target_sheet_name == "A:Z":
            target_sheet_name = "Master"
        
        from harvester import add_log
        add_log(f"🧬 [SyncEngine] Avvio estrazione dati per: {range_name}")
        try:
            creds = google_auth.get_credentials()
            service = build('sheets', 'v4', credentials=creds)
            
            res = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
            rows = res.get("values", [])
            
            # --- TRACCIAMENTO LOG UI ---
            from logger_utils import add_log
            
            if len(rows) <= 1: 
                add_log(f"⚠️ [The Loom] Foglio '{target_sheet_name}' vuoto o senza dati.")
                return {"imported": 0, "updated": 0}
                
            # --- FUNZIONE DI MATCHING LOCALE ---
            def match_header(h_low, key, defaults):
                lookups = [str(x).lower().strip() for x in defaults]
                if h_low in lookups: return True
                return any(x in h_low for x in lookups)

            # --- DISCOVERY DELLA RIGA DI INTESTAZIONE ---
            header_row_idx = None
            idx_brand = idx_model = idx_desc = idx_price = idx_col = idx_mat = idx_cond = None
            idx_acc = idx_hard = idx_dim = idx_drop = idx_loc = idx_size = idx_serial = None
            headers = []

            for r_idx, row in enumerate(rows[:10]):
                temp_headers = [str(h).lower().strip() for h in row]
                m_brand = m_model = m_price = m_desc = m_col = m_mat = m_cond = None
                m_acc = m_hard = m_dim = m_drop = m_loc = m_size = m_serial = None
                matches = 0
                
                for i, h_low in enumerate(temp_headers):
                    if m_brand is None and match_header(h_low, 'brand', self.mapping['brand']): 
                        m_brand = i; matches += 1
                    if m_model is None and match_header(h_low, 'model', self.mapping['model']): 
                        m_model = i; matches += 1
                    if m_price is None and match_header(h_low, 'price', self.mapping['price']): 
                        m_price = i; matches += 1
                    if m_desc is None and match_header(h_low, 'description', self.mapping['description']): 
                        m_desc = i
                    if m_col is None and match_header(h_low, 'color', self.mapping['color']): 
                        m_col = i
                    if m_mat is None and match_header(h_low, 'material', self.mapping['material']): 
                        m_mat = i
                    if m_size is None and match_header(h_low, 'size', self.mapping['size']): 
                        m_size = i
                    if m_serial is None and match_header(h_low, 'serial', self.mapping['serial']): 
                        m_serial = i
                
                if matches >= 2:
                    header_row_idx = r_idx
                    headers = temp_headers
                    idx_brand, idx_model, idx_price = m_brand, m_model, m_price
                    idx_desc, idx_col, idx_mat = m_desc, m_col, m_mat
                    idx_size, idx_serial = m_size, m_serial
                    # Altri campi opzionali se presenti nel loop
                    idx_cond, idx_acc, idx_hard = m_cond, m_acc, m_hard
                    idx_dim, idx_drop, idx_loc = m_dim, m_drop, m_loc
                    add_log(f"📍 [The Loom] Intestazione rilevata alla riga {r_idx + 1} ('{target_sheet_name}').")
                    break
            
            if header_row_idx is None:
                add_log(f"❌ [The Loom] Impossibile trovare intestazioni valide in '{target_sheet_name}'.")
                return {"imported": 0, "updated": 0, "error": "Header not found"}

            # Completamento indici
            idx_mat = idx_col = idx_cond = idx_acc = idx_hard = idx_dim = idx_drop = idx_loc = idx_desc = None
            for i, h in enumerate(headers):
                h_low = h.lower().strip()
                if idx_desc is None and match_header(h_low, 'description', self.mapping['description']): idx_desc = i
                if idx_mat is None and match_header(h_low, 'material', self.mapping['material']): idx_mat = i
                if idx_col is None and match_header(h_low, 'color', self.mapping['color']): idx_col = i
                if idx_cond is None and match_header(h_low, 'condition', self.mapping['condition']): idx_cond = i
                if idx_acc is None and match_header(h_low, 'acc', ['corredo', 'accessor', 'kit']): idx_acc = i
                if idx_hard is None and match_header(h_low, 'hw', ['hardware', 'metallo', 'oro', 'argento']): idx_hard = i
                if idx_dim is None and match_header(h_low, 'dim', ['misure', 'dimensioni', 'dimens']): idx_dim = i
                if idx_drop is None and match_header(h_low, 'drop', ['luce', 'drop']): idx_drop = i
                if idx_loc is None and match_header(h_low, 'loc', ['sede', 'magazzino']): idx_loc = i

            imported_count = 0
            updated_count = 0
            skipped_rows = []
            
            # Elaborazione righe reali
            for row_idx, row in enumerate(rows[header_row_idx+1:], start=header_row_idx+2):
                padded = row + [""] * (max(len(headers), len(row)))
                brand_val = self.normalize_field(padded[idx_brand], "brand") if idx_brand is not None else ""
                model_val = self.normalize_field(padded[idx_model]) if idx_model is not None else ""
                
                serial_val = self.normalize_field(padded[idx_serial]) if idx_serial is not None else ""
                
                # Accettiamo il prodotto se ha un Brand E (un Modello O un Seriale)
                if not brand_val or (not model_val and not serial_val):
                    add_log(f"⚠️ [The Loom] Salto riga {row_idx}: Identificativi mancanti (Brand/Modello/Seriale).")
                    skipped_rows.append({"row": row_idx, "data": f"{brand_val or '?'}/{model_val or '?'}", "reason": "Mancanti"})
                    continue
                
                desc_val = padded[idx_desc] if idx_desc is not None else ""
                price_float = 0.0
                if idx_price is not None:
                    try:
                        p_str = str(padded[idx_price]).replace("€", "").replace(",", ".").replace(" ", "").strip()
                        price_float = float(p_str)
                    except: pass

                # Verifica esistenza
                exists = db_session.query(Product).filter(
                    Product.original_sheets_row == row_idx,
                    Product.source_sheet == target_sheet_name
                ).first()

                if not exists:
                    # Dati iniziali per snapshot
                    current_snap = {
                        "brand": brand_val, "model": model_val, "price": price_float, 
                        "description": desc_val, "category": target_sheet_name
                    }
                    prod = Product(
                        brand=brand_val, model=model_val, description=desc_val,
                        price=price_float, source_sheet=target_sheet_name,
                        category=target_sheet_name,
                        original_sheets_row=row_idx, status=ProductStatus.Draft,
                        master_snapshot_json=json.dumps(current_snap),
                        has_master_conflict=0
                    )
                    idx_map = {
                        "material": idx_mat, "color": idx_col, "condition_grade": idx_cond,
                        "accessories_included": idx_acc, "hardware_type": idx_hard,
                        "dimensions": idx_dim, "handle_drop": idx_drop, "location": idx_loc,
                        "size": idx_size, "serial_number": idx_serial
                    }
                    for field, idx in idx_map.items():
                        if idx is not None and idx < len(padded): 
                            val = self.normalize_field(padded[idx], field)
                            setattr(prod, field, val)
                            current_snap[field] = val
                    
                    prod.master_snapshot_json = json.dumps(current_snap)
                    
                    # Calcolo integrità iniziale
                    try:
                        from harvester import HarvesterEngine
                        prod.match_confidence = HarvesterEngine.calculate_integrity(prod)
                    except:
                        prod.match_confidence = 0.0
                        
                    db_session.add(prod)
                    imported_count += 1
                else:
                    # CHANGE DETECTION LOGIC
                    incoming_data = {
                        "brand": brand_val, "model": model_val, "price": price_float, 
                        "description": desc_val, "category": target_sheet_name
                    }
                    idx_map = {
                        "material": idx_mat, "color": idx_col, "condition_grade": idx_cond,
                        "accessories_included": idx_acc, "hardware_type": idx_hard,
                        "dimensions": idx_dim, "handle_drop": idx_drop, "location": idx_loc
                    }
                    for field, idx in idx_map.items():
                        if idx is not None: incoming_data[field] = self.normalize_field(padded[idx], field)
                    
                    # Carichiamo vecchio snapshot
                    old_snap = {}
                    if exists.master_snapshot_json:
                        try:
                            old_snap = json.loads(exists.master_snapshot_json)
                        except: pass
                    else:
                        # Se non c'è snapshot, lo creiamo basandoci sui dati attuali del DB (Inizializzazione)
                        old_snap = {
                            "brand": exists.brand, "model": exists.model, "price": exists.price,
                            "description": exists.description, "category": exists.category,
                            "material": exists.material, "color": exists.color
                        }

                    # Confronto campi critici
                    is_changed = False
                    for key, val in incoming_data.items():
                        if str(old_snap.get(key)) != str(val):
                            is_changed = True
                            break
                    
                    if is_changed:
                        exists.has_master_conflict = 1
                        exists.master_snapshot_json = json.dumps(incoming_data)
                        
                        # Recalculate integrity for the "would-be" state if applied
                        try:
                            from harvester import HarvesterEngine
                            # Temporarily simulate the new data for score calculation
                            # (Optional: we could just wait for the user to apply, but seeing 
                            # the score impact might be useful)
                            exists.match_confidence = HarvesterEngine.calculate_integrity(exists)
                        except: pass
                        
                        updated_count += 1 # Conta come "rilevato"
                    else:
                        exists.has_master_conflict = 0
                        # Se tutto uguale, aggiorniamo comunque lo snapshot per sicurezza
                        exists.master_snapshot_json = json.dumps(incoming_data)

            db_session.commit()
            return {"imported": imported_count, "updated": updated_count, "skipped": skipped_rows}
            
        except Exception as e:
            from harvester import add_log
            add_log(f"❌ [The Loom] SYNC ERROR [{target_sheet_name}]: {e}")
            db_session.rollback()
            return {"imported": 0, "updated": 0, "error": str(e), "skipped": []}

engine = SyncEngine()
