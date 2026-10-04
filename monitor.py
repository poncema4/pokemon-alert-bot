"""Pokémon TCG monitor for the four core retailers.

Discord alerts and live-hit pins are limited to Target, Walmart, Best Buy and
GameStop. Niche shops remain map-only.

Alert policy:
  1. IN STOCK — verified availability signal on an already-known listing.
  2. NEW LISTING — handled separately, but only notified when stock is verified.
  3. AVAILABILITY UNKNOWN — tracked internally for accuracy, never sent to Discord.

A 403/429/timeout is UNKNOWN, never IN STOCK. UNKNOWN states are deliberately
separated from the verified-stock cooldown so a blocked check can never suppress
a later real restock alert.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
import advisor
from notify import alert, send_card

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 in Actions always has zoneinfo
    ZoneInfo = None

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
ALERTS_FILE = ROOT / "docs/alerts.json"
MARKET_FILE = ROOT / "docs/market.json"

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
DOMAINS = {
    "target": "target.com",
    "walmart": "walmart.com",
    "bestbuy": "bestbuy.com",
    "gamestop": "gamestop.com",
}
POKEMON_WORDS = (
    "pokemon", "pokémon", "etb", "elite trainer", "booster", "trading card", "tcg",
    "151", "prismatic evolutions", "destined rivals", "phantasmal flames", "white flare",
    "black bolt", "ascended heroes", "perfect order", "pitch black", "30th celebration",
    "30th anniversary",
)
IN_STOCK_HINTS = (
    "add to cart", "add to bag", "add to basket", "ship it", "shipping available",
    "available for shipping", "pickup today", "pick up today", "available for pickup", "low stock",
)
OUT_OF_STOCK_HINTS = ("out of stock", "sold out", "currently unavailable", "pre-order closed")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as exc:
        print(f"JSON load failed {path}: {exc}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
    if retailer == "target":
        return bool(re.match(r"^/p/-/a-\d+", path))
    if retailer == "walmart":
        return bool(re.match(r"^/ip/\d+", path)) or bool(re.match(r"^/ip/[^/]+/\d+", path))
    if retailer == "bestbuy":
        return path.startswith("/product/") or bool(re.match(r"^/site/.+/.+\.p", path))
    if retailer == "gamestop":
        return path.startswith("/toys-games/trading-cards/products/")
    return False


def extract_retailer_urls(retailer, text):
    candidates = re.findall(r'href=[\"\']([^\"\']+)', text, flags=re.I) + re.findall(r'https?://[^\"\'<>\\ ]+', text)
    patterns = {
        "walmart": r"/ip/(?:[^\"\'<>\\ ]+/)?\d+",
        "target": r"/p/-/A-\d+",
        "bestbuy": r"/(?:site|product)/[^\"\'<>\\ ]+",
        "gamestop": r"/toys-games/trading-cards/products/[^\"\'<>\\ ]+",
    }
    candidates += [BASE_URLS[retailer] + x for x in re.findall(patterns[retailer], text, flags=re.I)]
    links, seen = [], set()
    for href in candidates:
        full = clean_result_url(urljoin(BASE_URLS[retailer], href)).split('"')[0].split("'")[0]
        if retailer_url_is_valid(retailer, full) and full not in seen:
            seen.add(full)
            links.append(full)
    return links[:20]


def fallback_search(http, retailer, keyword):
    query = f'site:{DOMAINS[retailer]} "{keyword}"'
    for engine, endpoint in (
        ("DuckDuckGo", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
        ("Bing", f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US"),
    ):
        try:
            response = http.get(endpoint, timeout=7)
            print(f"  fallback {engine} -> HTTP {response.status_code} ({len(response.text)} bytes)")
            if response.status_code < 400:
                links = extract_retailer_urls(retailer, response.text)
                if links:
                    return links[:8]
        except Exception as exc:
            print(f"  fallback {engine} failed: {exc}")
    return []


def discover_products(http, retailer, keyword, timeout):
    try:
        response = http.get(SEARCH_URLS[retailer].format(q=quote_plus(keyword)), timeout=timeout)
        print(f"  search {retailer} '{keyword}' -> HTTP {response.status_code} ({len(response.text)} bytes)")
        if response.status_code < 400:
            links = extract_retailer_urls(retailer, response.text)
            if links:
                return links
    except Exception as exc:
        print(f"  direct search failed {retailer} / {keyword}: {exc}")
    print(f"  no usable direct links from {retailer}; using public-search fallback")
    return fallback_search(http, retailer, keyword)


def parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def extract_posted_time(text):
    patterns = [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"publishedAt"\s*:\s*"([^"]+)"',
        r'<meta[^>]+(?:property|name)=[\"\'](?:article:published_time|datePublished|publishdate)[\"\'][^>]+content=[\"\']([^\"\']+)',
        r'<time[^>]+datetime=[\"\']([^\"\']+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            parsed = parse_iso(html.unescape(m.group(1)))
            if parsed:
                return parsed
    return None


def extract_title(text, retailer):
    for pattern in (
        r'<meta[^>]+property=[\"\']og:title[\"\'][^>]+content=[\"\']([^\"\']+)',
        r'<title[^>]*>(.*?)</title>',
    ):
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
            if title:
                return title[:180]
    return f"{retailer.title()} Pokémon product"


def extract_structured_availability(text):
    compact = re.sub(r"\s+", " ", text, flags=re.S)
    values = re.findall(r'"availability"\s*:\s*"(?:https?://schema.org/)?([A-Za-z]+)"', compact, flags=re.I)
    if values:
        vals = [v.lower() for v in values]
        if any(v in ("instock", "limitedavailability") for v in vals):
            return True
        if any(v in ("outofstock", "soldout", "discontinued", "preorder") for v in vals):
            return False
    return None


CART_BUTTON = re.compile(r"<button\b([^>]*)>\s*add to (?:cart|bag|basket)\s*</button>", re.I | re.S)
# GameStop marks the real state on the page ("data-available") while its JSON-LD says InStock even when the item is
# unavailable (captured 2026-10-04: 30th Celebration ETB had data-available="false", no cart button, JSON-LD InStock).
AVAILABILITY_FLAG = re.compile(r'\bdata-available\s*=\s*"(true|false)"', re.I)
PRICE_PATTERN = re.compile(r'"price"\s*:\s*"?([0-9]{1,5}(?:\.[0-9]{1,2})?)"?')
BOT_WALL_MARKERS = ("robot or human", "px-captcha", "captcha.px-cdn", "press & hold", "access denied", "are you a human")


def classify_response(status, final_url, text):
    """Decide what a retailer response can tell us. Pure, so it is testable.

    Returns (stock, reason, signal): stock is True/False/None (None = UNKNOWN),
    reason says why (ok, blocked, http_<code>, no_signal), signal says how strong
    an in-stock reading is ("structured" data, weak "text" wording, or None).
    A bot wall often answers HTTP 200 after a redirect (Walmart sends /blocked), so the
    status code alone is never trusted.
    """
    if status is None:
        return None, "error", None
    path = urlparse(final_url or "").path.lower()
    if status >= 400:
        reason = "blocked" if status in (403, 429, 435) else f"http_{status}"
        return None, reason, None
    low = (text or "").lower()
    if path.startswith("/blocked") or any(marker in low for marker in BOT_WALL_MARKERS):
        return None, "blocked", None
    flags = {v.lower() for v in AVAILABILITY_FLAG.findall(text or "")}
    if len(flags) == 1:  # the page's own flag beats JSON-LD; mixed flags (e.g. related products) are ambiguous, so fall through
        available = flags == {"true"}
        return available, "ok", "page" if available else None
    structured = extract_structured_availability(text or "")
    if structured is not None:
        return structured, "ok", "structured" if structured else None
    if any(h in low for h in OUT_OF_STOCK_HINTS):
        return False, "ok", None
    if any(h in low for h in IN_STOCK_HINTS):
        # Target's server-rendered page ships a *disabled* "Add to cart" placeholder and fills in real stock later from an
        # API behind a captcha. A disabled cart button is a loading state, never evidence of stock.
        carts = CART_BUTTON.findall(text or "")
        if carts and all("disabled" in attrs.lower() for attrs in carts):
            return None, "cart_disabled", None
        return True, "ok", "text"
    return None, "no_signal", None


def extract_price(text):
    """The first listed price on the page (JSON-LD offer), or None."""
    m = PRICE_PATTERN.search(text or "")
    return float(m.group(1)) if m else None


def msrp_for(config, url):
    """Retail price for a listing from the config rules (first rule whose text appears in the URL), or None."""
    low = (url or "").lower()
    for rule in config.get("msrp_rules", []):
        if rule["match"].lower() in low:
            return float(rule["msrp"])
    return None


def check_product_page(http, retailer, url, timeout):
    fallback_title = f"{retailer.title()} Pokémon product"
    try:
        response = http.get(url, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        print(f"  product check unavailable: {exc}")
        return {"stock": None, "title": fallback_title, "posted_at": None, "http_status": None, "reason": "error", "signal": None}
    stock, reason, signal = classify_response(response.status_code, response.url, response.text)
    if reason not in ("ok", "no_signal"):
        print(f"  product page {reason} (HTTP {response.status_code}): {url}")
        return {"stock": None, "title": fallback_title, "posted_at": None, "http_status": response.status_code, "reason": reason, "signal": None}
    text = response.text
    return {"stock": stock, "title": extract_title(text, retailer), "posted_at": extract_posted_time(text), "http_status": response.status_code, "reason": reason, "signal": signal, "price": extract_price(text)}


HEALTH_FILE = ROOT / "docs/health.json"
BLIND_AFTER_HOURS = 24.0
PRUNE_AFTER_DAYS = 7
BLOCKED_STREAK_LIMIT = 3


def update_health(health, retailer, counts, now):
    """Fold one run's per-retailer counts into the health record; returns notices to send.

    counts = {"checked": n, "readable": n, "blocked": n, "errors": n}. A retailer is *blind*
    when a run checked pages but could read none of them. A blind spot is announced once after
    BLIND_AFTER_HOURS and again when reading recovers, so silence never means "nothing in stock".
    """
    entry = health.setdefault(retailer, {})
    entry.update(counts)
    entry["last_run"] = now.isoformat()
    notices = []
    if counts["checked"] and counts["readable"] == 0:
        entry.setdefault("blind_since", now.isoformat())
        since = datetime.fromisoformat(entry["blind_since"])
        if now - since >= timedelta(hours=BLIND_AFTER_HOURS) and not entry.get("blind_notified"):
            entry["blind_notified"] = True
            hours = int((now - since).total_seconds() // 3600)
            notices.append(("blind", retailer, f"{retailer.title()} has not been readable for {hours} h. Every check was blocked or empty, so this bot cannot see {retailer.title()} stock right now. Check it by hand."))
    elif counts["readable"]:
        entry["last_readable_at"] = now.isoformat()
        if entry.get("blind_notified"):
            notices.append(("recovered", retailer, f"{retailer.title()} is readable again."))
        entry.pop("blind_since", None)
        entry.pop("blind_notified", None)
    return notices


def should_prune(entry, seed, now):
    """Drop discovered listings that have never been readable for PRUNE_AFTER_DAYS (seed URLs stay)."""
    if seed:
        return False
    try:
        last_ok = entry.get("last_ok") or (entry["last_seen"] if entry.get("in_stock") is not None else None)  # legacy entries have no last_ok
        newest = datetime.fromisoformat(last_ok or entry.get("first_seen") or entry["last_seen"])
    except Exception:
        return False
    return now - newest > timedelta(days=PRUNE_AFTER_DAYS)


def clean_state(state):
    cleaned = {"schema_version": 4}
    for key, value in state.items():
        if key == "schema_version" or not isinstance(value, dict) or "::" not in key:
            continue
        source, url = key.split("::", 1)
        if source in SEARCH_URLS and retailer_url_is_valid(source, url) and (value.get("pokemon") is True or is_pokemon(url + " " + value.get("title", ""))):
            cleaned[key] = value
    return cleaned


def recently_stock_alerted(entry, now, hours):
    if not entry or not entry.get("last_stock_alert"):
        return False
    try:
        return now - datetime.fromisoformat(entry["last_stock_alert"].replace("Z", "+00:00")) < timedelta(hours=hours)
    except Exception:
        return False


def format_et(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ZoneInfo:
            return dt.astimezone(ZoneInfo("America/New_York")).strftime("%B %-d, %Y %-I:%M %p %Z")
        return dt.astimezone(timezone(timedelta(hours=-4))).strftime("%B %-d, %Y %-I:%M %p EDT")
    except Exception:
        return value


def record_alert(alerts, retailer, kind, title, url, verified, posted_at, detected_at, stock=None):
    now = datetime.now(timezone.utc)
    alerts.insert(0, {
        "ts": detected_at,
        "detected_at": detected_at,
        "posted_at": posted_at,
        "expires_at": (now + timedelta(minutes=15)).isoformat(),
        "kind": kind,
        "retailer": retailer,
        "title": title,
        "url": url,
        "verified": verified,
        "stock": stock,
        "online": True,
        "stores": [],
    })


def known_urls(state, retailer, exclude, now, cap=30):
    """Previously discovered listings worth re-checking every fast cycle: readable recently, newest first."""
    rows = []
    for key, entry in state.items():
        if not isinstance(entry, dict) or not key.startswith(retailer + "::"):
            continue
        url = key.split("::", 1)[1]
        if url in exclude or not entry.get("last_ok"):
            continue
        try:
            if now - datetime.fromisoformat(entry["last_ok"]) <= timedelta(days=PRUNE_AFTER_DAYS):
                rows.append((entry["last_ok"], url))
        except Exception:
            continue
    return [url for _, url in sorted(rows, reverse=True)[:cap]]


def main(discover=True):
    """One monitoring pass. discover=False is the fast pass: only seed + known listings, no searches."""
    config = load_json(CONFIG_FILE, {})
    state = clean_state(load_json(STATE_FILE, {}))
    alerts = load_json(ALERTS_FILE, [])
    keywords = config.get("keywords", [])
    retailers = [r for r in config.get("retailers", []) if r in SEARCH_URLS]
    cooldown = float(config.get("alert_cooldown_hours", 1))
    timeout = int(config.get("search_timeout_seconds", 8))
    ping = os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")
    map_url = config.get("map_url", "")
    http = requests.Session()
    http.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    sent = {"stock": 0, "unknown": 0}
    print(f"Retailers this run: {retailers}")
    print("Discord/live hits: BIG 4 ONLY (Target, Walmart, Best Buy, GameStop)")
    print("UNKNOWN Discord alerts: OFF (hard safety rule)")
    print("403/429/timeout: UNKNOWN internally, never IN STOCK")
    print("Niche shops: MAP ONLY — no Discord alerts")

    health = load_json(HEALTH_FILE, {})
    market_cache = load_json(MARKET_FILE, {})
    notices = []

    for retailer in retailers:
        seeds = list(dict.fromkeys(config.get("seed_urls", {}).get(retailer, [])))
        counts = {"checked": 0, "readable": 0, "blocked": 0, "errors": 0}
        streak = 0

        def check_url(url):
            nonlocal streak
            key = f"{retailer}::{url}"
            previous = state.get(key, {})
            result = check_product_page(http, retailer, url, timeout)
            in_stock = result["stock"]
            reason = result["reason"]
            counts["checked"] += 1
            if in_stock is not None:
                counts["readable"] += 1
                streak = 0
            else:
                counts["blocked" if reason == "blocked" or reason.startswith("http_") else "errors"] += 1
                streak += 1
            title = result["title"] if is_pokemon(result["title"] + " " + url) else f"{retailer.title()} Pokémon product"
            posted_at = result.get("posted_at")
            kind = None
            price = result.get("price")
            msrp = msrp_for(config, url)
            # Listings from before first_seen existed are not "new": never announce them.
            first_seen = previous.get("first_seen") or (previous.get("last_seen") if previous else now.isoformat())
            new_announced = previous.get("new_announced", True) if previous else False

            if previous and in_stock is True and previous.get("in_stock") is not True and not recently_stock_alerted(previous, now, cooldown):
                kind = "stock"
            elif previous and in_stock is None:
                sent["unknown"] += 1

            if kind == "stock":
                detected_at = now.isoformat()
                record_alert(alerts, retailer, kind, title, url, True, posted_at, detected_at, True)
                market = advisor.market_for(config, market_cache, title, url)
                send_card(advisor.build_card(retailer, kind, title, url, map_url, detected_at, result.get("signal"), price, msrp, market, ping))
                sent["stock"] += 1
                last_stock_alert = detected_at
            else:
                last_stock_alert = previous.get("last_stock_alert")

            state[key] = {
                "pokemon": True,
                "title": title,
                "in_stock": in_stock,
                "posted_at": posted_at,
                "first_seen": first_seen,
                "new_announced": new_announced,
                "last_seen": now.isoformat(),
                "last_ok": now.isoformat() if in_stock is not None else previous.get("last_ok"),
                "last_stock_alert": last_stock_alert,
                "http_status": result.get("http_status"),
                "reason": reason,
                "signal": result.get("signal"),
                "price": price,
                "msrp": msrp,
            }

        for url in seeds:
            if retailer_url_is_valid(retailer, url):
                check_url(url)
            else:
                print(f"  skipped invalid {retailer} URL: {url}")

        if counts["checked"] and streak >= BLOCKED_STREAK_LIMIT:
            print(f"  {retailer}: {streak} checks in a row unreadable; skipping discovery and extra checks this run")
        else:
            seen = set(seeds)
            extra = known_urls(state, retailer, seen, now)
            seen.update(extra)
            for keyword in (keywords if discover else []):
                print(f"Checking {retailer} / {keyword}")
                for url in discover_products(http, retailer, keyword, timeout):
                    if retailer_url_is_valid(retailer, url) and url not in seen:
                        extra.append(url)
                        seen.add(url)
            for url in extra:
                if streak >= BLOCKED_STREAK_LIMIT:
                    print(f"  {retailer}: {streak} checks in a row unreadable; skipping the rest this run")
                    break
                check_url(url)

        notices += update_health(health, retailer, counts, now)
        print(f"  {retailer} health: {counts}")

    for kind, retailer, message in notices:
        alert(f"{'⚠️ BLIND SPOT' if kind == 'blind' else '✅ RECOVERED'} · {retailer.title()}", message, ping=ping if kind == "blind" else False, tone="blind" if kind == "blind" else "ok")
    save_json(HEALTH_FILE, health)
    save_json(MARKET_FILE, market_cache)

    seed_keys = {f"{r}::{u}" for r, urls in config.get("seed_urls", {}).items() for u in urls}
    before = len(state)
    state = {k: v for k, v in state.items() if k == "schema_version" or not should_prune(v, k in seed_keys, now)}
    if len(state) != before:
        print(f"Pruned {before - len(state)} listings never readable for {PRUNE_AFTER_DAYS} days")

    alerts = [a for a in alerts if a.get("retailer", "").lower() in retailers]
    alerts = [a for a in alerts if not a.get("expires_at") or a.get("expires_at") > now.isoformat()]
    save_json(STATE_FILE, state)
    save_json(ALERTS_FILE, alerts[:100])
    print(f"Done. Verified Discord alerts sent this run: {sent['stock']}. UNKNOWN checks tracked silently: {sent['unknown']}. Tracked items: {len(state)-1}")


if __name__ == "__main__":
    main()
