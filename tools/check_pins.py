"""Re-check every store pin against OpenStreetMap, preferring the store's own record over an address guess.

Why: an address geocoder returns the point of the *address*, which in a shopping centre can be a neighbouring unit
(GameStop Kearny's address landed on a Taco Bell). OpenStreetMap usually has a record for the store itself, so:
  1. look for a store record (Overpass) with the exact chain name near the current pin  -> pin_source "osm-poi:<id>"
  2. otherwise geocode the street address (Nominatim)                                    -> pin_source "nominatim:<date>"

Network tool, not part of CI. `python tools/check_pins.py` reports the distance to each store's best point (over 300 ft is
flagged); add --fix to rewrite docs/stores.json. Both services ask for an identifying User-Agent and gentle use.
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
CHAINS = {"target": "Target", "walmart": "Walmart", "bestbuy": "Best Buy", "gamestop": "GameStop", "pokemoncenter": "Pok.mon Center"}
EXACT_NAME = re.compile(r"^(Target|Walmart( Supercenter)?|Best Buy|GameStop|Nintendo NEW YORK)$", re.I)


def feet_between(lat1, lng1, lat2, lng2):
    p = math.radians
    a = math.sin(p(lat2 - lat1) / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lng2 - lng1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(a)) * 5280


def pick_store_record(elements, near):
    """Nearest OSM element whose name is exactly the chain (not 'On Target Staffing', not a bus stop). Returns (lat, lng, id) or None."""
    best = None
    for element in elements:
        name = (element.get("tags") or {}).get("name", "")
        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lng = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is None or not EXACT_NAME.match(name):
            continue
        distance = feet_between(near[0], near[1], lat, lng)
        if best is None or distance < best[0]:
            best = (distance, lat, lng, f"{element['type']}{element['id']}")
    return best[1:] if best else None


def overpass(store):
    chain = CHAINS.get(store["retailer"])
    if not chain:
        return None
    query = f'[out:json][timeout:25];(nwr(around:1500,{store["lat"]},{store["lng"]})["name"~"{chain}",i];);out center tags;'
    for attempt in range(3):
        try:
            response = requests.post("https://overpass-api.de/api/interpreter", data={"data": query}, headers=HEADERS, timeout=45)
            response.raise_for_status()
            time.sleep(1.5)
            return pick_store_record(response.json()["elements"], (store["lat"], store["lng"]))
        except Exception:
            time.sleep(5)
    return None


def geocode(address):
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
        record = overpass(store)
        if record:
            lat, lng, source = record[0], record[1], f"osm-poi:{record[2]}"
        else:
            point = geocode(store["address"])
            if not point:
                print(f"{store['id']:26} nothing found")
                continue
            (lat, lng), source = point, f"nominatim:{date.today().isoformat()}"
        off = feet_between(store["lat"], store["lng"], lat, lng)
        print(f"{store['id']:26} {off:6.0f} ft  {source} {'<-- CHECK' if off > WARN_FEET else ''}")
        if fix:
            store["lat"], store["lng"], store["pin_source"] = round(lat, 6), round(lng, 6), source
    if fix:
        STORES.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("stores.json rewritten")


if __name__ == "__main__":
    main()
