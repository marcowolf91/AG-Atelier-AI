import json
import logging
from database import SessionLocal, Product, CategoryGovernance, CategoryRule

def add_governance_log(message: str):
    import datetime
    emoji = "🛡️"
    if "Error" in message or "❌" in message: emoji = "⚠️"
    elif "Corrected" in message or "✅" in message: emoji = "✨"
    
    clean_msg = f"{emoji} Governance: {message}"
    with open("harvester_debug.log", "a") as f:
        f.write(f"{datetime.datetime.now()} - {clean_msg}\n")

class GovernanceEngine:
    def __init__(self):
        pass

    def evaluate_rules(self, product: Product, rules: list[CategoryRule], applied_disjunctively: bool):
        """
        Valuta se un prodotto soddisfa le regole di una categoria.
        Mirroring della logica 'Smart Collection' di Shopify.
        """
        if not rules:
            return True # Nessuna regola = Nessun vincolo (o nessun match automatico)

        results = []
        product_tags = [t.strip().lower() for t in (product.tags or "").split(",") if t.strip()]
        
        for rule in rules:
            match = False
            col = rule.column.upper()
            rel = rule.relation.upper()
            cond = rule.condition.lower()

            # Estrazione valore da controllare
            target_val = ""
            if col == "TAG":
                # Per i tag, controlliamo se la condizione è presente nella lista
                if rel == "EQUALS": match = cond in product_tags
                elif rel == "CONTAINS": match = any(cond in t for t in product_tags)
                elif rel == "NOT_EQUALS": match = cond not in product_tags
            elif col == "VENDOR" or col == "BRAND":
                target_val = (product.brand or "").lower()
            elif col == "TITLE" or col == "MODEL":
                target_val = (product.model or "").lower()
            elif col == "TYPE":
                target_val = (product.category or "").lower()

            if col != "TAG":
                if rel == "EQUALS": match = target_val == cond
                elif rel == "CONTAINS": match = cond in target_val
                elif rel == "ENDS_WITH": match = target_val.endswith(cond)
                elif rel == "STARTS_WITH": match = target_val.startswith(cond)
                elif rel == "NOT_EQUALS": match = target_val != cond

            results.append(match)

        if applied_disjunctively:
            return any(results) # OR Logic
        else:
            return all(results) # AND Logic

    async def audit_all_products(self):
        """Analizza tutti i prodotti e segnala incongruenze di categorizzazione."""
        db = SessionLocal()
        try:
            products = db.query(Product).all()
            categories = db.query(CategoryGovernance).all()
            
            issues = []
            for p in products:
                # 1. Trova in quali categorie "Smart" dovrebbe trovarsi il prodotto
                matching_categories = []
                for cat in categories:
                    rules = db.query(CategoryRule).filter(CategoryRule.category_id == cat.id).all()
                    if self.evaluate_rules(p, rules, cat.applied_disjunctively == 1):
                        matching_categories.append(cat)
                
                # 2. Verifica se la categoria attuale (governance_category_id) è corretta
                current_cat = next((c for c in categories if c.id == p.governance_category_id), None)
                
                # Esempio: Il prodotto è in "Uomo" ma i tag suggeriscono "Donna" (o viceversa come il caso Gucci)
                if current_cat and current_cat not in matching_categories:
                    issues.append({
                        "product_id": p.id,
                        "sku": p.sku,
                        "title": f"{p.brand} {p.model}",
                        "issue": "Incongruenza Categoria/Tag",
                        "current_category": current_cat.name,
                        "suggested_categories": [c.name for c in matching_categories]
                    })
                elif not current_cat and matching_categories:
                     # Prodotto non categorizzato che potrebbe entrare in categorie smart
                     pass 

            return issues
        finally:
            db.close()

    async def apply_policy_tags(self, product_id: int):
        """Applica i tag obbligatori previsti dalla categoria di governance."""
        db = SessionLocal()
        try:
            p = db.query(Product).filter(Product.id == product_id).first()
            if not p or not p.governance_category_id:
                return False
            
            cat = db.query(CategoryGovernance).filter(CategoryGovernance.id == p.governance_category_id).first()
            if not cat or not cat.required_tags:
                return False
            
            # Applicazione policy e rimozione tag vietati
            policy_tags = [t.strip() for t in cat.required_tags.split(",") if t.strip()]
            forbidden_tags = [t.strip().lower() for t in (cat.forbidden_tags or "").split(",") if t.strip()]
            existing_tags = [t.strip() for t in (p.tags or "").split(",") if t.strip()]
            
            # 1. Aggiunge policy tags
            combined = list(set(existing_tags + policy_tags))
            
            # 2. Rimuove forbidden tags (case-insensitive)
            final_tags = []
            for t in combined:
                if t.lower() not in forbidden_tags:
                    final_tags.append(t)
                else:
                    add_governance_log(f"✂️ Tag vietato rimosso: {t} da {p.sku}")
            
            p.tags = ", ".join(final_tags)
            db.commit()
            add_governance_log(f"✅ Policy applicata a {p.sku} (Cat: {cat.name})")
            return True
        finally:
            db.close()
