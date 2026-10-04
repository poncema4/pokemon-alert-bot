"""Save the retailer pages exactly as the bot sees them (run from GitHub Actions: `snapshot.yml`).

GitHub's runners and a laptop are treated differently by retailers, so false positives and negatives can only be
studied from what the runner really receives. Pages land in snapshots/ together with a one-line summary of the
availability signals found in each, and the workflow uploads them as an artifact.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
import monitor  # noqa: E402

OUT = ROOT / "snapshots"
SIGNALS = (r'"availability"\s*:\s*"[^"]+"', r"InStock|OutOfStock|SoldOut|LimitedAvailability|PreOrder|BackOrder", r"Add to Cart|Add to cart|Not Available|Sold Out|Out of Stock|Currently unavailable|Notify Me|Check Availability|Pick up|Ship it|Ship to")


def main():
    config = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    http = requests.Session()
    http.headers.update(monitor.HEADERS)
    index = []
    for retailer, urls in config.get("seed_urls", {}).items():
        for n, url in enumerate(urls):
            name = f"{retailer}_{n}"
            try:
                response = http.get(url, timeout=20, allow_redirects=True)
                (OUT / f"{name}.html").write_text(response.text, encoding="utf-8")
                stock, reason, signal = monitor.classify_response(response.status_code, response.url, response.text)
                found = {}
                for pattern in SIGNALS:
                    for match in re.findall(pattern, response.text):
                        found[match] = found.get(match, 0) + 1
                row = {"file": name, "url": url, "status": response.status_code, "final_url": response.url, "bytes": len(response.text), "bot_says": [stock, reason, signal], "signals": found}
            except Exception as exc:
                row = {"file": name, "url": url, "error": str(exc)[:200]}
            index.append(row)
            print(json.dumps(row)[:600])
    # Listing pages: do they load over plain HTTP, and how many product links does the bot's extractor find on them?
    for retailer, pages in config.get("listing_pages", {}).items():
        for n, url in enumerate(pages):
            name = f"listing_{retailer}_{n}"
            try:
                response = http.get(url, timeout=25, allow_redirects=True)
                (OUT / f"{name}.html").write_text(response.text, encoding="utf-8")
                links = monitor.extract_retailer_urls(retailer, response.text) if response.status_code < 400 else []
                etbs = [u for u in links if "elite-trainer-box" in u]
                row = {"file": name, "url": url, "status": response.status_code, "bytes": len(response.text), "product_links": len(links), "etb_links": etbs[:25]}
            except Exception as exc:
                row = {"file": name, "url": url, "error": str(exc)[:200]}
            index.append(row)
            print(json.dumps(row)[:700])
    # GameStop fills its listing tiles from this JSON endpoint, keyed by product id: can the bot ask it directly?
    try:
        listing = (OUT / "listing_gamestop_0.html").read_text(encoding="utf-8")
        pids = list(dict.fromkeys(re.findall(r'data-pid="(\d+)"', listing)))[:20]
        endpoint = "https://www.gamestop.com/on/demandware.store/Sites-gamestop-us-Site/default/Tile-GetProductsJSON"
        for params in ({"pids": ",".join(pids)}, {"pid": pids[0]}):
            response = http.get(endpoint, params=params, timeout=25)
            (OUT / f"tile_{len(params)}.json").write_text(response.text[:200000], encoding="utf-8")
            row = {"file": "tile_endpoint", "params": list(params), "status": response.status_code, "bytes": len(response.text), "head": response.text[:600]}
            index.append(row)
            print(json.dumps(row)[:900])
    except Exception as exc:
        index.append({"file": "tile_endpoint", "error": str(exc)[:200]})
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
