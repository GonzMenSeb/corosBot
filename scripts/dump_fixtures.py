"""Dump live COROS responses to `fixtures/` for offline tests.

Fixtures are DEVELOPMENT-ONLY. The running app always retrieves live — `catalog.py`'s
own docstring says a cached feed reads as mocked, and `fixtures/` exists only so
`tests/test_contracts.py` can pin a wire shape without spending a live request on every
run. Nothing under `packages/` or `apps/` may import from `fixtures/`.

Run with: `PYTHONPATH=. ./.venv/bin/python scripts/dump_fixtures.py`
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "packages"))

from coros_core import catalog, ucp  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
COLLECTIONS_URL = f"{catalog.BASE}/collections.json?limit=250"
SEARCH_QUERY = {"query": "reloj gps", "context": ucp.CONTEXT, "pagination": {"limit": 3}}

SPACING = 1.5  # matches ucp.PACE_SECONDS — one caller, no reason to burst it


def _write_json(name: str, payload: object) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"{name}  ok")


async def main() -> None:
    OUT.mkdir(exist_ok=True)

    products = await catalog._get_json(catalog.PRODUCTS_URL, "products")
    _write_json("products.json", products)

    collections = await catalog._get_json(COLLECTIONS_URL, "collections")
    _write_json("collections.json", collections)

    blog = await catalog._get(catalog.BLOG_URL, "blog")
    (OUT / "blog.atom").write_bytes(blog.content)
    print("blog.atom  ok")

    await asyncio.sleep(SPACING)
    tools = await ucp.rpc("tools/list")
    _write_json("tools_list.json", tools)

    await asyncio.sleep(SPACING)
    search = await ucp.call_ucp("search_catalog", {"catalog": SEARCH_QUERY})
    _write_json("search_catalog.json", search)

    raw_products = products.get("products") or []
    if not raw_products:
        print("!! products.json carried no products, cannot probe get_product")
        return
    gid = f"gid://shopify/Product/{raw_products[0]['id']}"
    print(f"probe product: {raw_products[0]['title']}  {gid}")

    await asyncio.sleep(SPACING)
    product = await ucp.call_ucp("get_product", {"catalog": {"id": gid, "context": ucp.CONTEXT}})
    _write_json("get_product.json", product)

    await ucp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
