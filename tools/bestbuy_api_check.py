"""Check Best Buy's official API with the BESTBUY_API_KEY secret: prints what it answers for every SKU PokePing knows, and (with --search)
the Pokemon products it can find, so their SKUs can be added to `bestbuy_skus` in config/search_config.json.

Run from the bestbuy-api-check workflow. The key is never printed. Exit code 1 when the API cannot be used (no key, refused, no answer).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
import bestbuy_api  # noqa: E402

SEARCHES = (["pokemon", "elite", "trainer", "box"], ["pokemon", "ultra", "premium", "collection"], ["pokemon", "30th", "celebration"])


def main() -> int:
    key = os.environ.get("BESTBUY_API_KEY", "")
    if not key:
        print("BESTBUY_API_KEY is not set: add it as a repository secret (see the README).")
        return 1
    config = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8")) if (ROOT / "data" / "state.json").exists() else {}
    skus = set(config.get("bestbuy_skus", {}).values()) | {v.get("sku") for k, v in state.items() if k.startswith("bestbuy::") and isinstance(v, dict) and v.get("sku")}
    http = requests.Session()
    found = bestbuy_api.fetch({s for s in skus if s}, key, http)
    if not found:
        print("The API gave no answer for the known SKUs (bad key, key not activated yet, or an outage).")
        return 1
    print("KNOWN SKUs")
    for sku, r in sorted(found.items()):
        verdict = {True: "IN STOCK", False: "not available", None: "unknown"}[r["stock"]]
        print(f"  {sku}  {verdict:14} ${r['price']}  {r['title']}")
    if "--search" in sys.argv:
        print("\nPOKEMON PRODUCTS BEST BUY LISTS (add the ones you want to bestbuy_skus as product-code: sku)")
        seen = set()
        for words in SEARCHES:
            for p in bestbuy_api.search(words, key, http):
                if p["sku"] not in seen:
                    seen.add(p["sku"])
                    print(f"  {p['sku']}  {({True: 'IN STOCK', False: 'not available', None: 'unknown'})[p['stock']]:14} ${p['price']}  {p['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
