import asyncio
from database import SessionLocal, Product
from shopify_bridge import ShopifyBridge

async def main():
    bridge = ShopifyBridge()
    mutation_variant = """
    mutation productVariantUpdate($input: ProductVariantInput!) {
      productVariantUpdate(input: $input) {
        productVariant {
          id
          price
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    
    # Prendo l'ID del mocassino su shopify per testare. Dobbiamo trovarlo.
    # Faremo una query generica finta
    pass
