"""Re-check every store pin against its street address (OpenStreetMap Nominatim).

Network tool, not part of CI: run `python tools/check_pins.py` (add --fix to rewrite
docs/stores.json with the geocoded point). Nominatim asks for at most 1 request per
second and an identifying User-Agent, so this is deliberately slow and polite.
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

STORES = Path(__file__).resolve().parents[1] / "docs" / "stores.json"
HEADERS = {"User-Agent": "pokemon-alert-bot-pin-check/1.0 (personal project, github.com/poncema4/pokemon-alert-bot)"}
WARN_FEET = 300


def feet_between(lat1, lng1, lat2, lng2):
    p = math.radians
    a = math.sin(p(lat2 - lat1) / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lng2 - lng1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(a)) * 5280


def geocode(address):
    # Suite numbers confuse the geocoder, so try the full address first, then without the suite.
    for query in dict.fromkeys((address, re.sub(r" (Ste|Unit|Suite)\b[^,]*,", ",", address))):
        hits = requests.get("https://nominatim.openstreetmap.org/search", params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"}, headers=HEADERS, timeout=20).json()
        time.sleep(1.1)
        if hits:
            return float(hits[0]["lat"]), float(hits[0]["lon"])
    return None


def main():
    fix = "--fix" in sys.argv
    data = json.loads(STORES.read_text(encoding="utf-8"))
    for store in data["stores"]:
        point = geocode(store["address"])
        if not point:
            print(f"{store['id']:26} address not found")
            continue
        off = feet_between(store["lat"], store["lng"], *point)
        print(f"{store['id']:26} {off:6.0f} ft {'<-- CHECK' if off > WARN_FEET else ''}")
        if fix:
            store["lat"], store["lng"] = round(point[0], 6), round(point[1], 6)
            store["pin_source"] = f"nominatim:{date.today().isoformat()}"
    if fix:
        STORES.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("stores.json rewritten")


if __name__ == "__main__":
    main()
