"""Pokémon TCG monitor for the four core retailers.

Discord alerts and live-hit pins are limited to Target, Walmart, Best Buy and
GameStop. Niche shops remain map-only.

Alert types:
  1. IN STOCK — verified availability signal on an already-known listing.
  2. NEW LISTING — first discovery of a Big 4 product, regardless of stock state.
  3. AVAILABILITY UNKNOWN — an already-known product changed from a known stock
     state to an unverifiable state.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
from notify import alert

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
STORES_FILE = ROOT / "docs/stores.json"
ALERTS_FILE = ROOT / "docs/alerts.json"

SEARCH_URLS = {"target": "https://www.target.com/s?searchTerm={q}", "walmart": "https://www.walmart.com/search?q={q}", "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}", "gamestop": "https://www.gamestop.com/search/?q={q}&lang=default"}
BASE_URLS = {"target": "https://www.target.com", "walmart": "https://www.walmart.com", "bestbuy": "https://www.bestbuy.com", "gamestop": "https://www.gamestop.com"}
DOMAINS = {"target": "target.com", "walmart": "walmart.com", "bestbuy": "bestbuy.com", "gamestop": "gamestop.com"}
POKEMON_WORDS = ("pokemon", "pokémon", "etb", "elite trainer", "booster", "trading card", "tcg", "151", "prismatic evolutions", "destined rivals", "phantasmal flames", "white flare", "black bolt", "ascended heroes", "perfect order", "pitch black", "30th celebration", "30th anniversary")
IN_STOCK_HINTS = ("add to cart", "add to bag", "add to basket", "ship it", "shipping available", "available for shipping", "pickup today", "pick up today", "available for pickup", "low stock")
OUT_OF_STOCK_HINTS = ("out of stock", "sold out", "currently unavailable", "pre-order closed")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as exc:
        print(f"JSON load failed {path}: {exc}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_pokemon(text):
    return any(word in text.lower() for word in POKEMON_WORDS)


def clean_result_url(url):
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
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if host != DOMAINS[retailer] and not host.endswith("." + DOMAINS[retailer]):
        return False
    path = p.path.lower()
    if retailer == "target": return bool(re.match(r"^/p/-/a-\d+", path))
    if retailer == "walmart": return bool(re.match(r"^/ip/\d+", path))
    if retailer == "bestbuy": return path.startswith("/product/") or bool(re.match(r"^/site/.+/.+\.p", path))
    if retailer == "gamestop": return path.startswith("/toys-games/trading-cards/products/")
    return False


def extract_retailer_urls(retailer, text):
    candidates = re.findall(r'href=[\"\']([^\"\']+)', text, flags=re.I) + re.findall(r'https?://[^\"\'<>\\ ]+', text)
    patterns = {"walmart": r"/ip/\d+", "target": r"/p/-/A-\d+", "bestbuy": r"/(?:site|product)/[^\"\'<>\\ ]+", "gamestop": r"/toys-games/trading-cards/products/[^\"\'<>\\ ]+"}
    candidates += [BASE_URLS[retailer] + x for x in re.findall(patterns[retailer], text, flags=re.I)]
    links, seen = [], set()
    for href in candidates:
        full = clean_result_url(urljoin(BASE_URLS[retailer], href)).split('"')[0].split("'")[0]
        if retailer_url_is_valid(retailer, full) and full not in seen:
            seen.add(full); links.append(full)
    return links[:20]


def fallback_search(http, retailer, keyword):
    query = f'site:{DOMAINS[retailer]} "{keyword}"'
    for engine, endpoint in (("DuckDuckGo", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"), ("Bing", f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US")):
        try:
            response = http.get(endpoint, timeout=7)
            print(f"  fallback {engine} -> HTTP {response.status_code} ({len(response.text)} bytes)")
            if response.status_code < 400:
                links = extract_retailer_urls(retailer, response.text)
                if links: return links[:8]
        except Exception as exc:
            print(f"  fallback {engine} failed: {exc}")
    return []


def discover_products(http, retailer, keyword, timeout):
    try:
        response = http.get(SEARCH_URLS[retailer].format(q=quote_plus(keyword)), timeout=timeout)
        print(f"  search {retailer} '{keyword}' -> HTTP {response.status_code} ({len(response.text)} bytes)")
        if response.status_code < 400:
            links = extract_retailer_urls(retailer, response.text)
            if links: return links
    except Exception as exc:
        print(f"  direct search failed {retailer} / {keyword}: {exc}")
    print(f"  no usable direct links from {retailer}; using public-search fallback")
    return fallback_search(http, retailer, keyword)


def parse_iso(value):
    if not value: return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception: return None


def extract_posted_time(text):
    patterns = [r'"datePublished"\s*:\s*"([^"]+)"', r'"publishedAt"\s*:\s*"([^"]+)"', r'<meta[^>]+(?:property|name)=[\"\'](?:article:published_time|datePublished|publishdate)[\"\'][^>]+content=[\"\']([^\"\']+)', r'<time[^>]+datetime=[\"\']([^\"\']+)']
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            parsed = parse_iso(html.unescape(m.group(1)))
            if parsed: return parsed
    return None


def extract_title(text, retailer):
    for pattern in (r'<meta[^>]+property=[\"\']og:title[\"\'][^>]+content=[\"\']([^\"\']+)', r'<title[^>]*>(.*?)</title>'):
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
            if title: return title[:180]
    return f"{retailer.title()} Pokémon product"


def extract_structured_availability(text):
    compact = re.sub(r"\s+", " ", text, flags=re.S)
    values = re.findall(r'"availability"\s*:\s*"(?:https?://schema.org/)?([A-Za-z]+)"', compact, flags=re.I)
    if values:
        vals = [v.lower() for v in values]
        if any(v in ("instock", "limitedavailability") for v in vals): return True
        if any(v in ("outofstock", "soldout", "discontinued", "preorder") for v in vals): return False
    return None


def check_product_page(http, retailer, url, timeout):
    try:
        response = http.get(url, timeout=timeout, allow_redirects=True)
        if response.status_code >= 400:
            print(f"  product page HTTP {response.status_code}: {url}")
            return {"stock": None, "title": f"{retailer.title()} Pokémon product", "posted_at": None}
        text = response.text
    except Exception as exc:
        print(f"  product check unavailable: {exc}")
        return {"stock": None, "title": f"{retailer.title()} Pokémon product", "posted_at": None}
    low = text.lower()
    structured = extract_structured_availability(text)
    if structured is not None: stock = structured
    elif any(h in low for h in OUT_OF_STOCK_HINTS): stock = False
    elif any(h in low for h in IN_STOCK_HINTS): stock = True
    else: stock = None
    return {"stock": stock, "title": extract_title(text, retailer), "posted_at": extract_posted_time(text)}


def clean_state(state):
    cleaned = {"schema_version": 4}
    for key, value in state.items():
        if key == "schema_version" or not isinstance(value, dict) or "::" not in key: continue
        source, url = key.split("::", 1)
        if source in SEARCH_URLS and retailer_url_is_valid(source, url) and (value.get("pokemon") is True or is_pokemon(url + " " + value.get("title", ""))): cleaned[key] = value
    return cleaned


def recently_alerted(entry, now, hours):
    if not entry or not entry.get("last_alert"): return False
    try: return now - datetime.fromisoformat(entry["last_alert"].replace("Z", "+00:00")) < timedelta(hours=hours)
    except Exception: return False


def format_et(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=-4))).strftime("%B %-d, %Y %-I:%M %p EDT")
    except Exception: return value


def send_alert(retailer, kind, title, url, map_url, ping, posted_at, detected_at):
    labels = {"stock": "IN STOCK", "unknown": "AVAILABILITY UNKNOWN"}
    descriptions = {
        "stock": "The retailer page is showing a verified availability/cart signal.",
        "unknown": "The product is known to the bot, but the retailer page is not currently giving a reliable stock signal.",
    }
    lines = [f"**{title}**", descriptions[kind], "Refresh the product page yourself because stock can change or be hidden by retailer anti-bot systems."]
    lines.append(f"Time Posted: {format_et(posted_at)}" if posted_at else "Time Posted: Not published by the retailer.")
    lines.append(f"Detected: {format_et(detected_at)}")
    lines.append(f"Map: {map_url}")
    lines.append(f"Product: {url}")
    alert(f"{labels[kind]} — {retailer.title()}", "\n".join(lines), ping=ping)


def record_alert(alerts, retailer, kind, title, url, verified, posted_at, detected_at, stock=None):
    now = datetime.now(timezone.utc)
    alerts.insert(0, {"ts": detected_at, "detected_at": detected_at, "posted_at": posted_at, "expires_at": (now + timedelta(minutes=30)).isoformat(), "kind": kind, "retailer": retailer, "title": title, "url": url, "verified": verified, "stock": stock, "online": True, "stores": []})


def main():
    config = load_json(CONFIG_FILE, {})
    state = clean_state(load_json(STATE_FILE, {}))
    alerts = load_json(ALERTS_FILE, [])
    keywords = config.get("keywords", [])
    retailers = [r for r in config.get("retailers", []) if r in SEARCH_URLS]
    cooldown = float(config.get("alert_cooldown_hours", 1))
    timeout = int(config.get("search_timeout_seconds", 8))
    ping = os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")
    map_url = config.get("map_url", "")
    http = requests.Session(); http.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    sent = {"stock": 0, "unknown": 0}
    print(f"Retailers this run: {retailers}")
    print("Discord/live hits: BIG 4 ONLY (Target, Walmart, Best Buy, GameStop)")
    print("Alert policy: new listings are handled separately; known listings can alert IN STOCK or AVAILABILITY UNKNOWN")
    print("Niche shops: MAP ONLY — no Discord alerts")

    for retailer in retailers:
        urls = list(dict.fromkeys(config.get("seed_urls", {}).get(retailer, [])))
        seen = set(urls)
        for keyword in keywords:
            print(f"Checking {retailer} / {keyword}")
            for url in discover_products(http, retailer, keyword, timeout):
                if retailer_url_is_valid(retailer, url) and url not in seen: urls.append(url); seen.add(url)
        for url in urls:
            if not retailer_url_is_valid(retailer, url): continue
            key = f"{retailer}::{url}"
            previous = state.get(key, {})
            result = check_product_page(http, retailer, url, timeout)
            in_stock = result["stock"]
            title = result["title"] if is_pokemon(result["title"] + " " + url) else f"{retailer.title()} Pokémon product"
            posted_at = result.get("posted_at")
            last_alert = previous.get("last_alert")
            kind = None
            # First discovery is handled by notify_new_listings.py so a fresh
            # baseline never produces duplicate NEW + IN STOCK alerts.
            if previous and in_stock is True and previous.get("in_stock") is not True and not recently_alerted(previous, now, cooldown): kind = "stock"
            elif previous and previous.get("in_stock") is not None and in_stock is None and not recently_alerted(previous, now, cooldown): kind = "unknown"
            if kind:
                detected_at = now.isoformat()
                record_alert(alerts, retailer, kind, title, url, kind == "stock", posted_at, detected_at, in_stock)
                send_alert(retailer, kind, title, url, map_url, ping, posted_at, detected_at)
                sent[kind] += 1; last_alert = detected_at
            state[key] = {"pokemon": True, "title": title, "in_stock": in_stock, "posted_at": posted_at, "last_seen": now.isoformat(), "last_alert": last_alert}

    alerts = [a for a in alerts if a.get("retailer", "").lower() in retailers]
    alerts = [a for a in alerts if not a.get("expires_at") or a.get("expires_at") > now.isoformat()]
    save_json(STATE_FILE, state); save_json(ALERTS_FILE, alerts[:100])
    print(f"Done. Alerts sent this run: {sent}. Tracked items: {len(state)-1}")


if __name__ == "__main__": main()
