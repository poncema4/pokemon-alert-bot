"""Price advisor: compares a listing's price with its retail price and the live TCGplayer market price.

The Discord alert says what the price means, for example "BUY: LOW" or "ABOVE MARKET". Price never decides *whether*
to alert (in stock is what matters), only how the alert reads. Market prices are cached in docs/market.json and refreshed
by the watcher, so an alert normally needs no network call. Network access goes through an injected `fetch`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

from update_30th_prices import ENDPOINT, HEADERS

LOW_RATIO = 0.90   # at least 10% under market: a deal
HIGH_RATIO = 1.10  # more than 10% over market: pricey
RETAIL_TOLERANCE = 1.02
CACHE_MINUTES = 10  # an alert needs the price as it is now, so a cached price is trusted for minutes, not hours
NOISE = (
    r"pok[eé]mon trading card games?\s*:?", r"pok[eé]mon tcg\s*:?", r"[|:\-]\s*gamestop\b", r":\s*target\b", r"[|:\-]\s*best buy", r"[|:\-]\s*walmart(\.com| business supplies)?",
    r"\bwalmart\.com\b", r"\bmega evolution\b\s*\d*\s*[-—:]?", r"scarlet\s*(&|and)\s*violet\s*\d*\s*[-—:]?", r"\bsv\d+\b", r"\bpok[eé]mon\b", r"\btcg\b", r"[\[\]()®™:]",
)
DEFAULT_EXCLUDE = ("case", "pokemon center", "exclusive", "display")
CART_URLS = {"target": "https://www.target.com/co-cart", "walmart": "https://www.walmart.com/cart", "bestbuy": "https://www.bestbuy.com/cart",
             "gamestop": "https://www.gamestop.com/cart/", "pokemoncenter": "https://www.pokemoncenter.com/cart"}
WALMART_ITEM = re.compile(r"walmart\.com/ip/(?:[^/?#]+/)?(\d{6,})")


def add_to_cart_url(retailer, url):
    """A link that puts the item in YOUR cart when you tap it (you still check out yourself). Only where the store publishes one:
    Walmart's add-to-cart link takes the item id from the product URL. Other stores have no public link, so the product page's own button is used."""
    if retailer == "walmart":
        m = WALMART_ITEM.search(url or "")
        if m:
            return f"https://affil.walmart.com/cart/addToCart?items={m.group(1)}"
    return ""


RETAILER_NAMES = {"target": "Target", "walmart": "Walmart", "bestbuy": "Best Buy", "gamestop": "GameStop", "pokemoncenter": "Pokémon Center"}


def clean_title(title):
    out = re.sub(r"pok[eé]mon center", "pkmcenter", (title or "").lower(), flags=re.I)  # keep "Pokémon Center": it names a different product
    for pattern in NOISE:
        out = re.sub(pattern, " ", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).replace("pkmcenter", "pokemon center").strip(" -—|")
    if "elite trainer box" in out:  # retailers append the abbreviation: "... Elite Trainer Box ETB"
        out = re.sub(r"\betb\b", "", out).strip()
    else:
        out = re.sub(r"\betb\b", "elite trainer box", out)
    return re.sub(r"\s+", " ", out).strip()


def build_query(title, url, config):
    """(cache key, search text, words the TCGplayer name must contain, words it must not contain)."""
    low = (url or "").lower()
    for rule in config.get("market_rules", []):
        if rule["match"].lower() in low:
            return rule["id"], rule["query"], [w.lower() for w in rule["include"]], [w.lower() for w in rule.get("exclude", DEFAULT_EXCLUDE)]
    query = clean_title(title)
    words = [w for w in query.split() if len(w) > 1 or w.isdigit()]
    wanted_center = "pokemon center" in query  # the Pokémon Center box is sold as "(Exclusive)", so both words are wanted then
    exclude = [w for w in DEFAULT_EXCLUDE if w not in query and not (wanted_center and w == "exclusive")]
    return "auto:" + query, query, words, exclude


def pick(rows, include, exclude):
    """First search row whose name has every include word, none of the exclude words, and a real market price."""
    if not include:  # nothing to match on (e.g. an untitled listing): never guess a product
        return None
    include = [w.replace("-", " ") for w in include]
    exclude = [w.replace("-", " ") for w in exclude]
    for row in rows:
        name = (row.get("productName") or "").lower().replace("-", " ")  # "Ultra-Premium" and "ultra premium" are the same product
        if all(w in name for w in include) and not any(w in name for w in exclude) and (row.get("marketPrice") or 0) > 0:
            return row
    return None


def fetch_query(query):
    body = {
        "algorithm": "sales_synonym_v2", "from": 0, "size": 12,
        "filters": {"term": {"productLineName": ["pokemon"], "productTypeName": ["Sealed Products"]}, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {"term": {"sellerStatus": "Live", "channelId": 0}, "range": {"quantity": {"gte": 1}, "directInventory": {"gte": 1}}, "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}}, "settings": {"useFuzzySearch": True, "didYouMean": {}}, "sort": {},
    }
    last = None
    for attempt in range(2):  # one retry: a transient connection error must not cost an alert its price comparison
        try:
            response = requests.post(ENDPOINT.replace("q=&", "q=" + requests.utils.quote(query) + "&"), json=body, headers=HEADERS, timeout=8)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
    raise last


def lookup(query, include, exclude, fetch=None, now=None):
    fetch = fetch or fetch_query
    now = now or datetime.now(timezone.utc)
    row = pick(fetch(query)["results"][0]["results"], include, exclude)
    if not row:
        return None
    return {"market": round(float(row["marketPrice"]), 2), "low": row.get("lowestPrice"), "listings": int(row.get("totalListings") or 0),
            "name": row["productName"], "product_id": int(row["productId"]), "updated_at": now.isoformat()}


def market_for(config, cache, title, url, fetch=None, now=None):
    """Market info for a listing: fresh cache first, then one live lookup, then a stale cache entry, else None. Never raises."""
    now = now or datetime.now(timezone.utc)
    key, query, include, exclude = build_query(title, url, config)
    entry = cache.get(key)
    if entry and now - datetime.fromisoformat(entry["updated_at"]) < timedelta(minutes=CACHE_MINUTES):
        return entry
    try:
        fresh = lookup(query, include, exclude, fetch, now)
    except Exception:
        return entry  # a stale price is more useful than none, and the entry's own timestamp shows its age
    if fresh:
        cache[key] = fresh
        return fresh
    return entry


def refresh_cache(config, cache, items, fetch=None, now=None):
    """Refresh the cache for every (title, url) the bot tracks. Returns how many entries were refreshed."""
    now = now or datetime.now(timezone.utc)
    refreshed = 0
    for title, url in items:
        key, query, include, exclude = build_query(title, url, config)
        try:
            fresh = lookup(query, include, exclude, fetch, now)
        except Exception:
            continue
        if fresh:
            cache[key] = fresh
            refreshed += 1
    return refreshed


def verdict(price, msrp, market):
    """What the price means. Returns {label, tone, summary, retail}; tone is good / fair / high / unknown."""
    retail = ""
    if price and msrp:
        pct = round((price / msrp - 1) * 100)
        retail = "at retail" if price <= msrp * RETAIL_TOLERANCE else f"above MSRP (+{pct}%)"
    if not price:
        return {"label": "PRICE UNKNOWN", "tone": "unknown", "summary": "The page did not show a price.", "retail": retail}
    if market:
        ratio = price / market
        diff = abs(round((ratio - 1) * 100))
        if ratio <= LOW_RATIO:
            return {"label": "BUY: LOW", "tone": "good", "summary": f"${price:,.2f} is {diff}% below the TCGplayer market (${market:,.2f}).", "retail": retail}
        if ratio <= HIGH_RATIO:
            return {"label": "FAIR PRICE", "tone": "fair", "summary": f"${price:,.2f} is about the TCGplayer market (${market:,.2f}).", "retail": retail}
        return {"label": "ABOVE MARKET", "tone": "high", "summary": f"${price:,.2f} is {diff}% above the TCGplayer market (${market:,.2f}).", "retail": retail}
    if msrp:
        if price <= msrp * RETAIL_TOLERANCE:
            return {"label": "AT RETAIL", "tone": "good", "summary": f"${price:,.2f} is the retail price.", "retail": retail}
        return {"label": "ABOVE MSRP", "tone": "high", "summary": f"${price:,.2f} is above retail (${msrp:,.2f}); no TCGplayer price found to compare.", "retail": retail}
    return {"label": "PRICE UNKNOWN", "tone": "unknown", "summary": f"Listed at ${price:,.2f}; no retail or TCGplayer price to compare.", "retail": retail}


def age_text(iso, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        minutes = max(0, int((now - datetime.fromisoformat(iso)).total_seconds() // 60))
    except Exception:
        return "age unknown"
    if minutes < 1:
        return "updated just now"
    if minutes < 60:
        return f"updated {minutes} min ago"
    return f"updated {minutes // 60} h ago"


def eastern(iso):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%-I:%M %p %Z")
    except Exception:
        return iso


def build_card(retailer, kind, title, url, map_url, detected_at, signal, price, msrp, market, ping=False, now=None, confirmed=False):
    """Everything the Discord embed needs, as a plain dict (see notify.stock_embed)."""
    now = now or datetime.now(timezone.utc)
    return {
        "retailer": RETAILER_NAMES.get(retailer, retailer), "kind": kind, "title": title, "url": url, "map_url": map_url,
        "detected": eastern(detected_at), "timestamp": detected_at, "signal": signal, "price": price, "msrp": msrp, "ping": ping,
        "market": market, "market_age": age_text(market["updated_at"], now) if market else "",
        "market_url": f"https://www.tcgplayer.com/product/{market['product_id']}" if market else "",
        "verdict": verdict(price, msrp, market["market"] if market else None),
        "cart_url": CART_URLS.get(retailer, ""),
        "add_url": add_to_cart_url(retailer, url),
        "confirmed": confirmed,
    }


def refresh_watchlist(config, cache, fetch=None, now=None):
    """Cache the live TCGplayer market price of every watched Elite Trainer Box under "watch:<id>". Returns how many refreshed."""
    now = now or datetime.now(timezone.utc)
    refreshed = 0
    for item in config.get("watchlist", []):
        include = [w.lower() for w in item["include"]] + [item.get("product", "elite trainer box")] + [w.lower() for w in item.get("market_include", [])]
        exclude = [w for w in DEFAULT_EXCLUDE] + [w.lower() for w in item.get("exclude", [])]
        try:
            found = lookup(item["query"], include, exclude, fetch, now)
        except Exception:
            continue
        if found:
            cache[f"watch:{item['id']}"] = {**found, "label": item["label"], "msrp": item.get("msrp")}
            refreshed += 1
    return refreshed
