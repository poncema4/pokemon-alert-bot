"""Fast, Pokémon-only stock monitor for GitHub Actions.

Major retailers are checked for both online and local availability. Online product
hits are never blocked by the nearby-store radius; the radius is only used to add
local store pins to an alert. Direct product URLs are seeded for known releases so
retailer search failures cannot hide a drop that we already know exists.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin

import requests

from notify import alert

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
STORES_FILE = ROOT / "docs/stores.json"
ALERTS_FILE = ROOT / "docs/alerts.json"

SEARCH_URLS = {
    "target": "https://www.target.com/s?searchTerm={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "gamestop": "https://www.gamestop.com/search/?q={q}&lang=default",
}
BASE_URLS = {"target": "https://www.target.com", "walmart": "https://www.walmart.com", "bestbuy": "https://www.bestbuy.com", "gamestop": "https://www.gamestop.com"}
DOMAINS = {"target": "target.com", "walmart": "walmart.com", "bestbuy": "bestbuy.com", "gamestop": "gamestop.com"}

IN_STOCK_HINTS = (
    "add to cart", "add to bag", "add to basket", "ship it", "shipping available",
    "pickup today", "pick up today", "available for pickup", "in stock", "low stock",
)
OUT_OF_STOCK_HINTS = (
    "out of stock", "sold out", "currently unavailable", "not available", "unavailable",
    "coming soon", "pre-order closed",
)
POKEMON_WORDS = (
    "pokemon", "pokémon", "etb", "elite trainer", "booster", "trading card", "tcg",
    "151", "prismatic evolutions", "destined rivals", "phantasmal flames", "white flare",
    "black bolt", "ascended heroes", "perfect order", "pitch black", "30th celebration",
    "30th anniversary",
)
SLICKDEALS_RSS = "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"
REDDIT_DEALS = "https://www.reddit.com/r/PokemonTCGDeals/new.json?limit=15"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


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
    matches = []
    for store in stores:
        if store.get("retailer") != retailer or not store.get("monitored", False):
            continue
        if home is None:
            matches.append((999999, store))
            continue
        distance = haversine_miles(home[0], home[1], store["lat"], store["lng"])
        if distance <= radius_miles:
            matches.append((distance, store))
    matches.sort(key=lambda x: x[0])
    return [store for _, store in matches[:limit]]


def maps_link(store):
    query = store.get("maps_query") or f"{store['name']} {store['address']}"
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)


def retailer_from_text(text):
    lower = text.lower()
    for name in ("walmart", "target", "best buy", "bestbuy", "gamestop"):
        if name in lower:
            return name.replace(" ", "")
    return "promo"


def is_pokemon(text):
    lower = text.lower()
    return any(word in lower for word in POKEMON_WORDS)


def clean_result_url(url: str) -> str:
    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    for param in ("uddg", "url"):
        m = re.search(rf"[?&]{param}=([^&]+)", url)
        if m:
            candidate = unquote(m.group(1))
            if candidate.startswith("http"):
                return candidate
    return url


def retailer_url_is_valid(retailer, url):
    low = url.lower()
    if DOMAINS[retailer] not in low:
        return False
    if retailer == "target":
        return "/p/" in low
    if retailer == "walmart":
        return "/ip/" in low
    if retailer == "bestbuy":
        return "/site/" in low or "/product/" in low
    if retailer == "gamestop":
        return "/products/" in low or "/pokemon" in low or "/trading-card" in low
    return False


def extract_retailer_urls(retailer, text):
    candidates = []
    hrefs = re.findall(r'href=[\"\']([^\"\']+)', text, flags=re.I)
    candidates.extend(hrefs)
    # Retailer pages frequently place product URLs inside JSON instead of hrefs.
    candidates.extend(re.findall(r'https?://[^\"\'<>\\ ]+', text))
    if retailer == "walmart":
        candidates.extend("https://www.walmart.com" + x for x in re.findall(r"/ip/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", text))
    if retailer == "target":
        candidates.extend("https://www.target.com" + x for x in re.findall(r"/p/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", text))
    if retailer == "bestbuy":
        candidates.extend("https://www.bestbuy.com" + x for x in re.findall(r"/(?:site|product)/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", text))
    links, seen = [], set()
    for href in candidates:
        full = clean_result_url(urljoin(BASE_URLS[retailer], href))
        full = full.split('"')[0].split("'")[0]
        if retailer_url_is_valid(retailer, full) and full not in seen:
            seen.add(full)
            links.append(full)
    return links[:20]


def fallback_search(http, retailer, keyword):
    query = f'site:{DOMAINS[retailer]} "{keyword}"'
    results = []
    endpoints = [
        ("DuckDuckGo", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
        ("Bing", f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US"),
    ]
    for engine, endpoint in endpoints:
        try:
            response = http.get(endpoint, timeout=8)
            print(f"  fallback {engine} -> HTTP {response.status_code} ({len(response.text)} bytes)")
            if response.status_code >= 400:
                continue
            results.extend(extract_retailer_urls(retailer, response.text))
            if results:
                break
        except Exception as exc:
            print(f"  fallback {engine} failed: {exc}")
    return list(dict.fromkeys(results))[:8]


def discover_products(http, retailer, keyword, timeout=8):
    links = []
    try:
        response = http.get(SEARCH_URLS[retailer].format(q=quote_plus(keyword)), timeout=timeout)
        print(f"  search {retailer} '{keyword}' -> HTTP {response.status_code} ({len(response.text)} bytes)")
        if response.status_code < 400:
            links = extract_retailer_urls(retailer, response.text)
    except Exception as exc:
        print(f"  direct search failed {retailer} / {keyword}: {exc}")
    if links:
        return links
    print(f"  no usable direct links from {retailer}; public-search fallback")
    return fallback_search(http, retailer, keyword)


def check_product_page(http, url, timeout=8):
    try:
        response = http.get(url, timeout=timeout, allow_redirects=True)
        text = response.text.lower()
        if response.status_code >= 400:
            print(f"  product page HTTP {response.status_code}: {url}")
            return None
    except Exception as exc:
        print(f"  product check unavailable: {exc}")
        return None

    # Availability text wins over generic page words such as shipping/pickup.
    if any(hint in text for hint in OUT_OF_STOCK_HINTS):
        if any(hint in text for hint in ("add to cart", "add to bag", "add to basket")):
            return True
        return False
    if any(hint in text for hint in IN_STOCK_HINTS):
        return True
    return None


def clean_state(state):
    """Drop legacy/non-Pokémon state, especially the accidental Nike Slickdeals key."""
    cleaned = {"schema_version": 2}
    for key, value in state.items():
        if key == "schema_version":
            continue
        if key.startswith(("slickdeals::", "reddit::")):
            # Feed entries are intentionally re-seeded after cleanup, but only
            # Pokémon feed items can ever be written back by this version.
            continue
        if "::" not in key:
            continue
        retailer, url = key.split("::", 1)
        if retailer not in SEARCH_URLS:
            continue
        if is_pokemon(url) or value.get("pokemon") is True:
            cleaned[key] = value
    return cleaned


def recently_alerted(entry, now, hours):
    if not entry or not entry.get("last_alert"):
        return False
    try:
        last = datetime.fromisoformat(entry["last_alert"].replace("Z", "+00:00"))
        return now - last < timedelta(hours=hours)
    except Exception:
        return False


def record_alert(alerts, kind, retailer, title, url, stores, home, radius, ttl_minutes, verified, online=True):
    pins = nearby_stores(stores, retailer, home, radius)
    now = datetime.now(timezone.utc)
    entry = {
        "ts": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "kind": kind,
        "retailer": retailer,
        "title": title,
        "url": url,
        "verified": verified,
        "online": online,
        "stores": [{"id": s["id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"]} for s in pins],
    }
    alerts.insert(0, entry)
    return entry, pins


def send_stock_alert(retailer, title, url, pins, map_url, ping, verified):
    if verified is True:
        headline = f"IN STOCK — {retailer.title()}"
        note = "Online product page is showing an availability/cart signal."
    else:
        headline = f"POSSIBLE POKÉMON HIT — {retailer.title()}"
        note = "Product URL was found but stock could not be verified from the runner. Open the link immediately."
    lines = [title, note, "Online availability is monitored independently of the local store radius."]
    if pins:
        lines.append("Nearby stores:")
        lines.extend(f"- {p['name']}: {maps_link(p)}" for p in pins)
    else:
        lines.append("No monitored local store was within the configured radius; this does not suppress an online alert.")
    if map_url:
        lines.append(f"Map: {map_url}")
    alert(headline, "\n".join(lines), url, ping=ping)


def prune_alerts(alerts):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    kept = []
    for item in alerts:
        try:
            ts = datetime.fromisoformat(item["ts"].replace("Z", "+00:00"))
            if ts >= cutoff and item.get("kind") != "test":
                kept.append(item)
        except Exception:
            continue
    return kept[:100]


def feed_items(http, url):
    try:
        response = http.get(url, timeout=8)
        if response.status_code >= 400:
            print(f"  feed HTTP {response.status_code}: {url}")
            return []
        root = ET.fromstring(response.text)
    except Exception as exc:
        print(f"  feed failed: {exc}")
        return []
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        body = re.sub("<[^>]+>", " ", (item.findtext("description") or "")).strip()
        if title and link:
            out.append({"title": title, "url": link, "body": body[:600]})
    return out


def reddit_items(http):
    try:
        response = http.get(REDDIT_DEALS, timeout=8, headers={**HEADERS, "User-Agent": "pokemon-alert-bot/6.0"})
        if response.status_code >= 400:
            print(f"  reddit HTTP {response.status_code}")
            return []
        children = response.json().get("data", {}).get("children", [])
    except Exception as exc:
        print(f"  reddit failed: {exc}")
        return []
    out = []
    for child in children:
        data = child.get("data", {})
        title = data.get("title") or ""
        if title:
            out.append({"title": title, "url": data.get("url") or "", "body": data.get("selftext") or ""})
    return out


def main():
    config = load_json(CONFIG_FILE, {})
    state = clean_state(load_json(STATE_FILE, {}))
    store_obj = load_json(STORES_FILE, {"stores": []})
    stores = store_obj.get("stores", [])
    alerts = load_json(ALERTS_FILE, [])
    home_obj = store_obj.get("home", {})
    home = (home_obj.get("lat"), home_obj.get("lng")) if home_obj.get("lat") is not None else None

    keywords = config.get("keywords", [])
    retailers = [r for r in config.get("retailers", []) if r in SEARCH_URLS]
    radius = float(config.get("nearby_radius_miles", 12))
    ttl = int(config.get("alert_ttl_minutes", 30))
    cooldown = float(config.get("alert_cooldown_hours", 1))
    timeout = int(config.get("search_timeout_seconds", 8))
    ping = os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")
    map_url = config.get("map_url", "")
    http = session()
    now = datetime.now(timezone.utc)
    sent = 0

    print(f"Retailers this run: {retailers}")
    print(f"Nearby radius: {radius:.1f} miles | online alerts ignore radius | cooldown: {cooldown:g}h")
    print("Price thresholds: DISABLED — availability/restock and qualifying Pokémon feeds only")
    print("Failure policy: FAIL OPEN — a matching direct product URL can alert even when stock cannot be verified")

    if os.environ.get("SEND_TEST_ALERT", "").lower() in ("1", "true", "yes"):
        alert("Pokemon Alert Bot test", "Discord is connected. This is a one-time manual test alert.", map_url, ping=ping)
        sent += 1

    for retailer in retailers:
        configured = list(dict.fromkeys(config.get("seed_urls", {}).get(retailer, [])))
        seen_urls = set(configured)
        for keyword in keywords:
            print(f"Checking {retailer} / {keyword}")
            for url in discover_products(http, retailer, keyword, timeout):
                if url not in seen_urls:
                    configured.append(url)
                    seen_urls.add(url)

        for url in configured:
            key = f"{retailer}::{url}"
            previous = state.get(key, {})
            in_stock = check_product_page(http, url, timeout)
            title = next((k for k in keywords if is_pokemon(k) and any(part in url.lower() for part in k.lower().split() if len(part) > 4)), retailer.title() + " Pokémon product")
            should_alert = False
            if in_stock is True:
                should_alert = previous.get("in_stock") is not True or recently_alerted(previous, now, cooldown)
            elif in_stock is None and not recently_alerted(previous, now, max(cooldown, 6)):
                # Fail-open candidate alert. This is especially important for
                # Best Buy/GameStop/Walmart anti-bot responses.
                should_alert = True
            if should_alert:
                _, pins = record_alert(alerts, "stock" if in_stock is True else "candidate", retailer, title, url, stores, home, radius, ttl, in_stock, online=True)
                send_stock_alert(retailer, title, url, pins, map_url, ping, in_stock)
                sent += 1
                last_alert = now.isoformat()
            else:
                last_alert = previous.get("last_alert")
            state[key] = {
                "pokemon": True,
                "in_stock": in_stock,
                "last_seen": now.isoformat(),
                "last_alert": last_alert,
            }

    # Deal/community feeds are strictly Pokémon-filtered. Legacy state is not
    # allowed to reintroduce arbitrary products into state.json.
    print("Checking Slickdeals frontpage RSS...")
    for item in feed_items(http, SLICKDEALS_RSS):
        text = item["title"] + " " + item["body"]
        if not is_pokemon(text):
            continue
        key = f"slickdeals::{item['url']}"
        if state.get(key):
            continue
        state[key] = {"seen": True, "pokemon": True, "title": item["title"]}
        alert("POKÉMON DEAL FEED", item["title"], item["url"], ping=ping)
        sent += 1

    print("Checking r/PokemonTCGDeals...")
    for item in reddit_items(http):
        text = item["title"] + " " + item["body"]
        if not is_pokemon(text):
            continue
        key = f"reddit::{item['url']}"
        if state.get(key):
            continue
        state[key] = {"seen": True, "pokemon": True, "title": item["title"]}
        alert("POKÉMON COMMUNITY DEAL", item["title"], item["url"], ping=ping)
        sent += 1

    alerts = prune_alerts(alerts)
    save_json(STATE_FILE, state)
    save_json(ALERTS_FILE, alerts)
    print(f"Done. Alerts sent this run: {sent}. Tracked Pokémon items: {len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
