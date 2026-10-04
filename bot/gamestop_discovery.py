"""Find new GameStop Elite Trainer Boxes (and Ultra-Premium Collections) without being told their URLs.

GameStop's Pokémon listing pages carry their product ids server-side (`data-pid`), and
`gamestop.com/products/-/<id>.html` redirects to the full product page for any id. So discovery is: read the listing pages,
resolve each id the bot has not seen, and keep the Pokémon products whose title says Elite Trainer Box (or another wanted
phrase from config `discover_titles`). A new Mega Evolution-era set therefore appears on its own the day GameStop lists it.
Every id is resolved once and cached in data/gamestop_pids.json (a rejected id is rechecked after 7 days).
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

PID = re.compile(r'data-pid="(\d+)"')
PRODUCT_PATH = "/toys-games/trading-cards/products/"
RECHECK = timedelta(days=7)


def listing_pids(text):
    """Product ids on a listing page, in page order, no duplicates."""
    return list(dict.fromkeys(PID.findall(text or "")))


def is_wanted(title, url, phrases):
    """A Pokémon trading-card product (not a graded single) whose title contains one of the wanted phrases."""
    low = f"{title} {url}".lower().replace("-", " ")
    return PRODUCT_PATH in url and "pokemon" in low.replace("é", "e") and any(p.lower().replace("-", " ") in low for p in phrases)


def resolve(http, pid, canonical, timeout=15):
    """The canonical product URL and title for a GameStop product id, or None."""
    response = http.get(f"https://www.gamestop.com/products/-/{pid}.html", timeout=timeout, allow_redirects=True)
    if response.status_code >= 400:
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.S)
    title = html.unescape(re.sub(r"\s+", " ", match.group(1))).replace("| GameStop", "").strip() if match else ""
    return {"url": canonical(response.url), "title": title}


def discover(http, config, cache, canonical, now=None, fetch_limit=12, timeout=15):
    """URLs of wanted products found on the listing pages. Resolves at most `fetch_limit` unseen ids per call. Never raises."""
    now = now or datetime.now(timezone.utc)
    phrases = config.get("discover_titles", ["elite trainer box"])
    pids = []
    for page in config.get("listing_pages", {}).get("gamestop", []):
        try:
            response = http.get(page, timeout=timeout + 10)
            if response.status_code < 400:
                pids += [p for p in listing_pids(response.text) if p not in pids]
        except Exception:
            continue
    fetched = 0
    for pid in pids:
        known = cache.get(pid)
        if known:
            age = now - datetime.fromisoformat(known["checked_at"])
            if known["wanted"] or age < RECHECK:
                continue
        if fetched >= fetch_limit:
            continue
        fetched += 1
        try:
            found = resolve(http, pid, canonical, timeout)
        except Exception:
            continue
        if found:
            cache[pid] = {**found, "wanted": is_wanted(found["title"], found["url"], phrases), "checked_at": now.isoformat()}
    return [cache[p]["url"] for p in pids if cache.get(p, {}).get("wanted")]
