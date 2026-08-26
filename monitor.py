"""
Pokemon TCG stock + promo alert bot
-----------------------------------
Runs on GitHub Actions (free). Checks Target and Walmart search pages,
plus public deal/news feeds so Best Buy-style promos still surface
without hitting Best Buy/GameStop (those sites block GitHub's IPs).

Alerts go to Discord. Writes docs/alerts.json for the live map.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

from notify import alert

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
STORES_FILE = ROOT / "docs" / "stores.json"
ALERTS_FILE = ROOT / "docs" / "alerts.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_URLS = {
    "target": "https://www.target.com/s?searchTerm={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
}

PRODUCT_LINK_PATTERNS = {
    "target": re.compile(r'href="(/p/[^"]+)"'),
    "walmart": re.compile(r'href="(/ip/[^"]+)"'),
}

BASE_URLS = {
    "target": "https://www.target.com",
    "walmart": "https://www.walmart.com",
}

PRICE_PATTERN = re.compile(r"\$(\d{1,4}\.\d{2})")
OUT_OF_STOCK_HINTS = ["out of stock", "sold out", "unavailable", "not available"]
IN_STOCK_HINTS = ["add to cart", "ship it", "add to bag", "shipping"]

SLICKDEALS_RSS = (
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"
)
POKEMON_NEWS_RSS = "https://www.pokemon.com/us/pokemon-news/rss"
REDDIT_DEALS = "https://www.reddit.com/r/PokemonTCGDeals/new.json?limit=15"

DEAL_WORDS = (
    "pokemon",
    "pokémon",
    "etb",
    "elite trainer",
    "booster",
    "tcg",
    "trading card",
)


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def expected_price_for(keyword: str, expected_prices: dict):
    for k, v in expected_prices.items():
        if k.lower() in keyword.lower():
            return v
    return None


def nearest_stores(stores: list, retailer: str, limit: int = 3):
    matches = [s for s in stores if s.get("retailer") == retailer]
    if not matches:
        matches = stores[:limit]
    return matches[:limit]


def maps_link(store: dict) -> str:
    q = quote_plus(f"{store['name']} {store['address']}")
    return f"https://www.google.com/maps/dir/?api=1&destination={q}"


def discover_products(http: requests.Session, retailer: str, keyword: str):
    url = SEARCH_URLS[retailer].format(q=quote_plus(keyword))
    try:
        r = http.get(url, timeout=12)
        print(f"  search {retailer} '{keyword}' -> HTTP {r.status_code} ({len(r.text)} bytes)")
        r.raise_for_status()
    except Exception as e:
        print(f"  search failed {retailer} / '{keyword}': {e}")
        return []

    links = set(PRODUCT_LINK_PATTERNS[retailer].findall(r.text))
    if not links:
        print(f"  no product links on {retailer} search page (often a bot wall from GitHub IPs)")
        return []
    full = [BASE_URLS[retailer] + link if link.startswith("/") else link for link in links]
    return full[:4]


def check_product_page(http: requests.Session, url: str):
    try:
        r = http.get(url, timeout=12)
        text = r.text.lower()
    except Exception as e:
        print(f"  product check failed: {e}")
        return None, None

    in_stock = None
    if any(h in text for h in OUT_OF_STOCK_HINTS):
        in_stock = False
    if any(h in text for h in IN_STOCK_HINTS):
        in_stock = True

    price = None
    match = PRICE_PATTERN.search(r.text)
    if match:
        try:
            price = float(match.group(1))
        except ValueError:
            pass
    return in_stock, price


def is_pokemon_deal(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in DEAL_WORDS)


def fetch_rss_items(http: requests.Session, url: str):
    try:
        r = http.get(url, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"  RSS failed {url}: {e}")
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "body": re.sub("<[^>]+>", " ", desc)[:240]})
    return items


def fetch_reddit_deals(http: requests.Session):
    try:
        r = http.get(
            REDDIT_DEALS,
            timeout=12,
            headers={**HEADERS, "User-Agent": "pokemon-alert-bot/2.0 (personal monitor)"},
        )
        r.raise_for_status()
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:
        print(f"  reddit deals failed: {e}")
        return []

    out = []
    for child in children:
        d = child.get("data", {})
        title = d.get("title") or ""
        permalink = d.get("permalink") or ""
        url = d.get("url") or ""
        if not title:
            continue
        out.append(
            {
                "title": title,
                "url": url or f"https://www.reddit.com{permalink}",
                "body": (d.get("link_flair_text") or "")[:200],
            }
        )
    return out


def record_alert(alerts: list, kind: str, retailer: str, title: str, url: str, stores: list, extra: dict | None = None):
    pins = nearest_stores(stores, retailer)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "retailer": retailer,
        "title": title,
        "url": url,
        "stores": [{"id": s["id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"]} for s in pins],
    }
    if extra:
        entry.update(extra)
    alerts.insert(0, entry)
    return entry, pins


def main():
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})
    store_data = load_json(STORES_FILE, {"stores": []})
    stores = store_data.get("stores", [])
    alerts = load_json(ALERTS_FILE, [])

    keywords = config.get("keywords", [])
    retailers = [r for r in config.get("retailers", ["target", "walmart"]) if r in SEARCH_URLS]
    expected_prices = config.get("expected_prices", {})
    deal_threshold = config.get("deal_threshold_percent", 15)
    ping = os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")
    map_url = config.get("map_url", "")

    http = session()
    sent = 0

    print(f"Retailers this run: {retailers}")
    print("Best Buy / GameStop are map-only (GitHub Actions IPs are blocked by those sites).")

    for retailer in retailers:
        for keyword in keywords:
            print(f"Checking {retailer} / {keyword}")
            urls = discover_products(http, retailer, keyword)
            for url in urls:
                key = f"{retailer}::{url}"
                prev = state.get(key, {})
                in_stock, price = check_product_page(http, url)
                if in_stock is None and price is None:
                    continue

                new_entry = {"in_stock": in_stock, "price": price}

                # First time we see a URL we only seed memory. Alert on restocks after that.
                if prev and in_stock and prev.get("in_stock") is not True:
                    entry, pins = record_alert(
                        alerts, "stock", retailer, keyword, url, stores, {"price": price}
                    )
                    nearby = "\n".join(f"- {p['name']}: {maps_link(p)}" for p in pins)
                    body = f"{keyword}\n{nearby}"
                    if map_url:
                        body += f"\nMap: {map_url}"
                    alert(
                        f"IN STOCK — {retailer.title()}",
                        body,
                        url,
                        ping=ping,
                    )
                    sent += 1

                expected = expected_price_for(keyword, expected_prices)
                if prev and price and expected:
                    discount_pct = (1 - (price / expected)) * 100
                    already = prev.get("deal_alerted_price") == price
                    if discount_pct >= deal_threshold and not already:
                        record_alert(
                            alerts,
                            "deal",
                            retailer,
                            keyword,
                            url,
                            stores,
                            {"price": price, "expected": expected, "discount_pct": round(discount_pct)},
                        )
                        alert(
                            f"DEAL — {retailer.title()}",
                            f"{keyword} at ${price:.2f} (normally ~${expected:.2f}, {discount_pct:.0f}% off)",
                            url,
                            ping=ping,
                        )
                        new_entry["deal_alerted_price"] = price
                        sent += 1

                state[key] = new_entry

    seed_feeds = not any(k.startswith(("slickdeals::", "pokenews::", "reddit::")) for k in state)

    print("Checking Slickdeals frontpage RSS for Pokemon promos...")
    for item in fetch_rss_items(http, SLICKDEALS_RSS):
        if not is_pokemon_deal(item["title"] + " " + item["body"]):
            continue
        key = f"slickdeals::{item['url']}"
        if state.get(key):
            continue
        retailer = "bestbuy" if "best buy" in (item["title"] + item["body"]).lower() else "promo"
        state[key] = {"seen": True}
        if seed_feeds:
            continue
        record_alert(alerts, "promo", retailer, item["title"], item["url"], stores)
        alert("PROMO / DEAL FEED", item["title"], item["url"], ping=ping)
        sent += 1

    print("Checking Pokemon.com news RSS...")
    for item in fetch_rss_items(http, POKEMON_NEWS_RSS):
        blob = (item["title"] + " " + item["body"]).lower()
        if not any(w in blob for w in ("elite trainer", "etb", "promotion", "promo", "anniversary", "release", "product")):
            continue
        key = f"pokenews::{item['url']}"
        if state.get(key):
            continue
        state[key] = {"seen": True}
        if seed_feeds:
            continue
        record_alert(alerts, "event", "pokemon", item["title"], item["url"], stores)
        alert("POKEMON EVENT / NEWS", item["title"], item["url"], ping=False)
        sent += 1

    print("Checking r/PokemonTCGDeals...")
    for item in fetch_reddit_deals(http):
        if not is_pokemon_deal(item["title"]):
            continue
        key = f"reddit::{item['url']}"
        if state.get(key):
            continue
        retailer_guess = "target"
        title_l = item["title"].lower()
        for name in ("walmart", "target", "best buy", "bestbuy", "gamestop", "pokemon center"):
            if name in title_l:
                retailer_guess = name.replace(" ", "")
                break
        state[key] = {"seen": True}
        if seed_feeds:
            continue
        record_alert(alerts, "community", retailer_guess, item["title"], item["url"], stores)
        alert("COMMUNITY DEAL", item["title"], item["url"], ping=ping)
        sent += 1

    alerts[:] = alerts[:60]
    save_json(STATE_FILE, state)
    save_json(ALERTS_FILE, alerts)
    print(f"Done. Alerts sent this run: {sent}. Tracked items in state: {len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
