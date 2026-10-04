"""Refresh the 30th Celebration guide from TCGplayer's public marketplace search.

Matching is by TCGplayer product id (stable), never by guessing from names. Rules that keep the guide honest:
  * every product records when its own price was last refreshed (`market_updated_at`) and its source;
  * a price that jumps more than 4x either way in one hour is treated as a glitch and ignored (flagged);
  * if the request fails or returns too little, prior values stay and the file says it is stale: `updated_at`
    only moves when prices were really refreshed, `checked_at` always moves;
  * the "chase ceiling" list is the real top cards by market price, not a presale guess.
Network access goes through `fetch_search`, so tests run on captured responses.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]  # repo root (this file lives in bot/)
DATA_FILE = ROOT / "docs" / "30th_prices.json"
ENDPOINT = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=true&mpfev=3060"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tcgplayer.com",
    "Referer": "https://www.tcgplayer.com/",
}
# guide product id -> TCGplayer product id(s). A list means "the typical price across variants" (median).
PRODUCT_IDS = {
    "night-upc": [704191], "day-upc": [704190], "pc-etb": [704144], "etb": [704143], "ditto": [704189],
    "booster-bundle": [704171], "poster": [704153], "binder": [704169], "mew-figure": [704193],
    "mewtwo-figure": [704194], "greninja-box": [704168], "sylveon-box": [704155], "tech-lucario": [704146],
    "tech-exeggutor": [704145], "knockout": [704152], "blister": [704148], "battle-espeon": [704187],
    "battle-umbreon": [704188], "tin-sylveon": [718674], "tin-greninja": [718673],
    "mini-tins": [704176, 704177, 704178, 704179, 704180, 704181, 704182, 704183, 704184, 704185],
}
MAX_JUMP = 4.0
MIN_COVERAGE = 0.8  # share of tracked products that must refresh for the file to count as freshly updated


def search_body(product_type, size, sort=None, offset=0):
    body = {
        "algorithm": "sales_synonym_v2", "from": offset, "size": size,
        "filters": {"term": {"productLineName": ["pokemon"], "setName": ["me-30th-celebration"], "productTypeName": [product_type]}, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {"term": {"sellerStatus": "Live", "channelId": 0}, "range": {"quantity": {"gte": 1}, "directInventory": {"gte": 1}}, "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": sort or {},
    }
    return body


def fetch_search(body):
    response = requests.post(ENDPOINT, json=body, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return response.json()


PAGE_SIZE = 50  # the endpoint rejects anything larger


def rows(payload):
    return payload["results"][0]["results"]


def fetch_all(fetch, product_type, sort=None, limit=200):
    """Page through the search (PAGE_SIZE at a time) and return one payload holding every row up to `limit`."""
    collected, offset, total = [], 0, None
    while offset < (total if total is not None else 1) and offset < limit:
        payload = fetch(search_body(product_type, PAGE_SIZE, sort, offset))
        page = rows(payload)
        total = int(payload["results"][0].get("totalResults") or len(page))
        collected += page
        if not page:
            break
        offset += PAGE_SIZE
    return {"results": [{"totalResults": total or len(collected), "results": collected}]}


def sealed_prices(payload):
    """{tcgplayer product id: {market, low, listings}} for rows that have a usable market price."""
    out = {}
    for row in rows(payload):
        price = row.get("marketPrice")
        if price and price > 0:
            out[int(row["productId"])] = {"market": round(float(price), 2), "low": row.get("lowestPrice"), "listings": int(row.get("totalListings") or 0)}
    return out


def apply_prices(data, prices, now):
    """Update products in place from `prices`; returns (updated ids, flagged ids, missing ids)."""
    updated, flagged, missing = [], [], []
    stamp = now.isoformat()
    for product in data.get("products", []):
        ids = PRODUCT_IDS.get(product.get("id"))
        found = [prices[i] for i in (ids or []) if i in prices]
        if not found:
            missing.append(product.get("id"))
            continue
        market = round(statistics.median(p["market"] for p in found), 2)
        old = product.get("market")
        if old and not (old / MAX_JUMP <= market <= old * MAX_JUMP):
            flagged.append(product["id"])
            product["market_flag"] = f"ignored {market} (more than {MAX_JUMP:g}x away from {old})"
            continue
        product.pop("market_flag", None)
        product["market"] = market
        product["market_updated_at"] = stamp
        product["market_source"] = "tcgplayer"
        product["listings"] = sum(p["listings"] for p in found)
        lows = [p["low"] for p in found if p.get("low")]
        product["low"] = round(min(lows), 2) if lows else None
        product.pop("market_note", None)
        updated.append(product["id"])
    return updated, flagged, missing


def chase_cards(payload, limit=8):
    """The real top cards by market price (replaces the old hand-written presale guesses)."""
    cards = []
    for row in rows(payload):
        price = row.get("marketPrice")
        if price and price > 0:
            cards.append({"name": row["productName"], "rarity": row.get("rarityName") or "", "tcg_value": f"${float(price):,.2f} market", "market": round(float(price), 2), "listings": int(row.get("totalListings") or 0)})
    cards.sort(key=lambda c: -c["market"])
    return [{"rank": i + 1, **c, "note": f"{c['listings']} listings on TCGplayer"} for i, c in enumerate(cards[:limit])]


def refresh(data, fetch=fetch_search, now=None):
    """Pure-ish core: returns the updated data dict. Never raises; failures leave prior values and mark the file stale."""
    now = now or datetime.now(timezone.utc)
    data["checked_at"] = now.isoformat()
    try:
        prices = sealed_prices(fetch_all(fetch, "Sealed Products"))
        cards = chase_cards(fetch(search_body("Cards", 12, {"field": "market-price", "order": "desc"})))
    except Exception as exc:  # network, HTTP, or an unexpected response shape
        data["status"] = "stale"
        data["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return data
    updated, flagged, missing = apply_prices(data, prices, now)
    total = len(data.get("products", [])) or 1
    coverage = len(updated) / total
    data["refresh"] = {"updated": len(updated), "flagged": flagged, "missing": missing, "total": total}
    if coverage >= MIN_COVERAGE:
        data["updated_at"] = now.isoformat()
        data["status"] = "live"
        data.pop("last_error", None)
        if cards:
            data["hits"] = cards
    else:
        data["status"] = "stale"
        data["last_error"] = f"only {len(updated)} of {total} products refreshed"
    data["source"] = "TCGplayer marketplace search (market price by product id)"
    return data


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data = refresh(data)
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    info = data.get("refresh", {})
    print(f"30th prices: status={data['status']} updated={info.get('updated')}/{info.get('total')} flagged={info.get('flagged')} missing={info.get('missing')} error={data.get('last_error')}")


if __name__ == "__main__":
    main()
