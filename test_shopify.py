import asyncio
import json
import httpx
from shopify_bridge import ShopifyBridge

async def check():
    b = ShopifyBridge()
    query = """
    {
      products(first: 5, query: "title:*Louis Vuitton X Nike*") {
        edges {
          node {
            id
            title
            variants(first: 1) {
              edges {
                node {
                  price
                  sku
                }
              }
            }
          }
        }
      }
    }
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(b.api_url, headers={"X-Shopify-Access-Token": b.token, "Content-Type": "application/json"}, json={"query": query})
        print(json.dumps(res.json(), indent=2))

asyncio.run(check())
