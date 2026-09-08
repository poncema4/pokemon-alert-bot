"""Refresh 30th Celebration sealed-product prices from public TCGplayer search pages.

This is intentionally conservative: if TCGplayer cannot be parsed or a value is
missing, the existing value is preserved. Presale card prices with tiny sales
volumes are not promoted to 'market value'.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "docs" / "30th_prices.json"
URL = "https://www.tcgplayer.com/search/pokemon/me-30th-celebration?productLineName=pokemon&setName=me-30th-celebration&view=grid"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def market_for(text: str, product_name: str):
    compact = re.sub(r"\s+", " ", text)
    escaped = re.escape(product_name)
    pattern = rf"{escaped}.*?Market Price:\$([0-9,]+(?:\.[0-9]+)?)"
    match = re.search(pattern, compact, flags=re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    try:
        response = requests.get(URL, headers=HEADERS, timeout=20)
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        print(f"TCGplayer refresh failed; preserving prior values: {exc}")
        return

    aliases = {
        "night-upc": "30th Celebration Ultra-Premium Collection [Night]",
        "day-upc": "30th Celebration Ultra-Premium Collection [Day]",
        "pc-etb": "30th Celebration Pokemon Center Elite Trainer Box",
        "etb": "30th Celebration Elite Trainer Box",
        "ditto": "30th Celebration Ditto Premium Collection",
        "mew-figure": "30th Celebration Figure Collection [Mew]",
        "mewtwo-figure": "30th Celebration Figure Collection [Mewtwo]",
        "booster-bundle": "30th Celebration Booster Bundle",
    }

    changed = 0
    for product in data.get("products", []):
        alias = aliases.get(product.get("id"))
        if not alias:
            continue
        value = market_for(text, alias)
        if value is not None and value != product.get("market"):
            product["market"] = value
            changed += 1

    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["status"] = "tcgplayer_auto_refresh"
    DATA_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"30th TCGplayer refresh complete. Changed {changed} product prices.")


if __name__ == "__main__":
    main()
