"""Pokémon TCG stock/deal monitor for GitHub Actions.

Checks configured retailer search pages and public deal feeds, deduplicates alerts,
limits map hits to nearby stores, and expires map hits automatically.
Retailer pages may block GitHub-hosted runners; failures are reported honestly.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests

from notify import alert

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
STORES_FILE = ROOT / "docs/stores.json"
ALERTS_FILE = ROOT / "docs/alerts.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_URLS = {
    "target": "https://www.target.com/s?searchTerm={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "gamestop": "https://www.gamestop.com/search/?q={q}&lang=default",
}
BASE_URLS = {
    "target": "https://www.target.com",
    "walmart": "https://www.walmart.com",
    "bestbuy": "https://www.bestbuy.com",
    "gamestop": "https://www.gamestop.com",
}

OUT_OF_STOCK_HINTS = (
    "out of stock", "sold out", "unavailable", "not available", "currently unavailable"
)
IN_STOCK_HINTS = (
    "add to cart", "add to bag", "ship it", "shipping", "pickup", "pick up"
)
SLICKDEALS_RSS = "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"
REDDIT_DEALS = "https://www.reddit.com/r/PokemonTCGDeals/new.json?limit=15"
DEAL_WORDS = (
    "pokemon", "pokémon", "etb", "elite trainer", "booster", "tcg", "trading card",
    "151", "prismatic evolutions", "destined rivals", "phantasmal flames",
    "white flare", "black bolt", "ascended heroes", "perfect order", "pitch black"
)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as exc:
        print(f"JSON load failed {path}: {exc}")
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def haversine_miles(a_lat, a_lng, b_lat, b_lng):
    radius = 3958.7613
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(x))


def nearby_stores(stores, retailer, home, radius_miles, limit=5):
    if not home:
        return [s for s in stores if s.get("retailer") == retailer][:limit]
    matches = []
    for store in stores:
        if store.get("retailer") != retailer:
            continue
        distance = haversine_miles(home[0], home[1], store["lat"], store["lng"])
        if distance <= radius_miles:
            matches.append((distance, store))
    matches.sort(key=lambda x: x[0])
    return [store for _, store in matches[:limit]]


def maps_link(store):
    return "https://www.google.com/maps/dir/?api=1&destination=" + quote_plus(
        f"{store['name']} {store['address']}"
    )


def retailer_from_text(text):
    lower = text.lower()
    for name in ("walmart", "target", "best buy", "bestbuy", "gamestop"):
        if name in lower:
            return name.replace(" ", "")
    return "promo"


def product_links_from_html(retailer, html):
    hrefs = re.findall(r'href=[\"\']([^\"\']+)', html, flags=re.I)
    links, seen = [], set()
    for href in hrefs:
        full = urljoin(BASE_URLS[retailer], href)
        low = full.lower()
        if retailer == "target" and "/p/" not in low:
            continue
        if retailer == "walmart" and "/ip/" not in low:
            continue
        if retailer == "bestbuy" and "/site/" not in low:
            continue
        if retailer == "gamestop" and not any(x in low for x in ("/products/", "/pokemon", "/trading-card")):
            continue
        if any(x in low for x in ("searchpage", "/search", "javascript:", "#")):
            continue
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links[:8]


def discover_products(http, retailer, keyword):
    try:
        response = http.get(SEARCH_URLS[retailer].format(q=quote_plus(keyword)), timeout=15)
        print(f"  search {retailer} '{keyword}' -> HTTP {response.status_code} ({len(response.text)} bytes)")
        response.raise_for_status()
    except Exception as exc:
        print(f"  search failed {retailer} / {keyword}: {exc}")
        return []
    links = product_links_from_html(retailer, response.text)
    if not links:
        print(f"  no product links from {retailer}; retailer may be blocking GitHub runner IP")
    return links


def check_product_page(http, url):
    try:
        response = http.get(url, timeout=15)
        text = response.text.lower()
    except Exception as exc:
        print(f"  product check failed: {exc}")
        return None

    # Prefer explicit purchase controls. If a page has both an old OOS phrase and
    # a current purchase control, treat the purchase control as the current signal.
    if any(hint in text for hint in IN_STOCK_HINTS):
        return True
    if any(hint in text for hint in OUT_OF_STOCK_HINTS):
        return False
    return None


def is_pokemon_deal(text):
    lower = text.lower()
    return any(word in lower for word in DEAL_WORDS)


def fetch_rss_items(http, url):
    try:
        response = http.get(url, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception as exc:
        print(f"  RSS failed {url}: {exc}")
        return []
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "body": re.sub("<[^>]+>", " ", description)[:500]})
    return items


def fetch_reddit_deals(http):
    try:
        response = http.get(
            REDDIT_DEALS,
            timeout=15,
            headers={**HEADERS, "User-Agent": "pokemon-alert-bot/4.0"},
        )
        response.raise_for_status()
        children = response.json().get("data", {}).get("children", [])
    except Exception as exc:
        print(f"  reddit deals failed: {exc}")
        return []
    items = []
    for child in children:
        data = child.get("data", {})
        title = data.get("title") or ""
        if title:
            items.append({
                "title": title,
                "url": data.get("url") or f"https://www.reddit.com{data.get('permalink', '')}",
                "body": (data.get("link_flair_text") or "")[:300],
            })
    return items


def record_alert(alerts, kind, retailer, title, url, stores, home, radius, ttl_minutes):
    pins = nearby_stores(stores, retailer, home, radius)
    now = datetime.now(timezone.utc)
    entry = {
        "ts": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "kind": kind,
        "retailer": retailer,
        "title": title,
        "url": url,
        "stores": [
            {"id": s["id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"]}
            for s in pins
        ],
    }
    alerts.insert(0, entry)
    return entry, pins


def prune_alerts(alerts, history_days=7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    kept = []
    for item in alerts:
        if item.get("kind") == "test":
            continue
        try:
            timestamp = datetime.fromisoformat(item["ts"].replace("Z", "+00:00"))
            if timestamp >= cutoff:
                kept.append(item)
        except Exception:
            continue
    return kept[:100]


def send_stock_alert(retailer, keyword, url, pins, map_url, ping):
    nearby = "\n".join(f"- {p['name']}: {maps_link(p)}" for p in pins) or "No configured nearby store matched this retailer."
    body = f"{keyword}\n{nearby}"
    if map_url:
        body += f"\nMap: {map_url}"
    alert(f"IN STOCK — {retailer.title()}", body, url, ping=ping)


def main():
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})
    store_obj = load_json(STORES_FILE, {"stores": []})
    stores = store_obj.get("stores", [])
    alerts = load_json(ALERTS_FILE, [])
    home_obj = store_obj.get("home", {})
    home = (home_obj.get("lat"), home_obj.get("lng")) if home_obj.get("lat") is not None else None

    keywords = config.get("keywords", [])
    retailers = [r for r in config.get("retailers", []) if r in SEARCH_URLS]
    radius = float(config.get("nearby_radius_miles", 12))
    ttl = int(config.get("alert_ttl_minutes", 30))
    ping = os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")
    map_url = config.get("map_url", "")
    http = session()
    sent = 0

    print(f"Retailers this run: {retailers}")
    print(f"Nearby radius: {radius:.1f} miles | live-hit TTL: {ttl} minutes")
    print("Price thresholds: DISABLED — stock/restock and qualifying deal feeds only")

    if os.environ.get("SEND_TEST_ALERT", "").lower() in ("1", "true", "yes"):
        alert("Pokemon Alert Bot test", "Discord is connected. This is a one-time manual test alert.", map_url, ping=ping)
        sent += 1

    for retailer in retailers:
        for keyword in keywords:
            print(f"Checking {retailer} / {keyword}")
            for url in discover_products(http, retailer, keyword):
                key = f"{retailer}::{url}"
                previous = state.get(key)
                in_stock = check_product_page(http, url)
                if in_stock is None:
                    continue

                # A first observation seeds state. Later OOS -> in-stock transitions
                # create alerts, preventing a fresh deployment from spamming every
                # product that happens to be available on the first scan.
                if previous is not None and in_stock and previous.get("in_stock") is not True:
                    _, pins = record_alert(alerts, "stock", retailer, keyword, url, stores, home, radius, ttl)
                    send_stock_alert(retailer, keyword, url, pins, map_url, ping)
                    sent += 1

                state[key] = {"in_stock": in_stock, "last_seen": datetime.now(timezone.utc).isoformat()}

    seed_feeds = not any(key.startswith(("slickdeals::", "reddit::")) for key in state)

    print("Checking Slickdeals frontpage RSS...")
    for item in fetch_rss_items(http, SLICKDEALS_RSS):
        if not is_pokemon_deal(item["title"] + " " + item["body"]):
            continue
        key = f"slickdeals::{item['url']}"
        if state.get(key):
            continue
        state[key] = {"seen": True}
        if seed_feeds:
            continue
        retailer = retailer_from_text(item["title"] + " " + item["body"])
        record_alert(alerts, "promo", retailer, item["title"], item["url"], stores, home, radius, ttl)
        alert("PROMO / DEAL FEED", item["title"], item["url"], ping=ping)
        sent += 1

    print("Checking r/PokemonTCGDeals...")
    for item in fetch_reddit_deals(http):
        if not is_pokemon_deal(item["title"]):
            continue
        key = f"reddit::{item['url']}"
        if state.get(key):
            continue
        state[key] = {"seen": True}
        if seed_feeds:
            continue
        retailer = retailer_from_text(item["title"])
        record_alert(alerts, "community", retailer, item["title"], item["url"], stores, home, radius, ttl)
        alert("COMMUNITY DEAL", item["title"], item["url"], ping=ping)
        sent += 1

    alerts = prune_alerts(alerts)
    save_json(STATE_FILE, state)
    save_json(ALERTS_FILE, alerts)
    print(f"Done. Alerts sent this run: {sent}. Tracked items: {len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
