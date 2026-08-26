"""
Pokemon TCG Stock + Deal Alert Bot (v2 - search-based, Discord-only)
---------------------------------------------------------------------
You give it search keywords (not individual product URLs). It searches
Target, Walmart, and Best Buy's own search pages for matches, tracks
what it finds, and alerts you on Discord when:
  - A matching product is newly in stock
  - A matching product's price looks like a real deal vs. your
    expected/normal price for that keyword

What this does NOT do (on purpose):
  - Does not auto-purchase/checkout anything
  - Does not use proxies, stealth browser plugins, or CAPTCHA solvers to
    evade bot-detection. Retailer search pages can occasionally block or
    rate-limit automated requests - when that happens that retailer's
    check just gets skipped for that run, it doesn't crash the bot.
  - Price extraction is a best-effort text match, not guaranteed exact -
    always confirm the price on the actual page before buying.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

import requests

STATE_FILE = Path(__file__).parent / "state.json"
CONFIG_FILE = Path(__file__).parent / "search_config.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal stock-checker)"}

SEARCH_URLS = {
    "target": "https://www.target.com/s?searchTerm={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "gamestop": "https://www.gamestop.com/search/?q={q}",
}

PRODUCT_LINK_PATTERNS = {
    "target": re.compile(r'href="(/p/[^"]+)"'),
    "walmart": re.compile(r'href="(/ip/[^"]+)"'),
    "bestbuy": re.compile(r'href="(/site/[^"]+\.p\?[^"]*)"'),
    # GameStop's product links follow /products/<name>/<id>.html - unverified
    # against a live page, so if this returns 0 links, tell me and I'll adjust it.
    "gamestop": re.compile(r'href="(/[^"]+/products/[^"]+\.html)"'),
}

BASE_URLS = {
    "target": "https://www.target.com",
    "walmart": "https://www.walmart.com",
    "gamestop": "https://www.gamestop.com",
    "bestbuy": "https://www.bestbuy.com",
}

PRICE_PATTERN = re.compile(r"\$(\d{1,4}\.\d{2})")
OUT_OF_STOCK_HINTS = ["out of stock", "sold out", "unavailable", "not available"]
IN_STOCK_HINTS = ["add to cart", "ship it", "add to bag", "shipping"]


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_discord(message: str, ping_everyone: bool = True):
    if not DISCORD_WEBHOOK_URL:
        print("No Discord webhook configured, skipping alert.")
        return
    content = f"@everyone {message}" if ping_everyone else message
    payload = {
        "content": content,
        # Explicitly allow the @everyone mention to actually ping -
        # without this, some servers/webhook configs will post the text
        # "@everyone" without it triggering a real notification.
        "allowed_mentions": {"parse": ["everyone"]},
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Discord send failed: {e}")


def maps_link_for(retailer: str, near_location: str) -> str:
    q = quote_plus(f"{retailer} near {near_location}")
    return f"https://www.google.com/maps/search/{q}"


def discover_products(retailer: str, keyword: str):
    """Search the retailer's own site search page for the keyword and
    pull out product links. Returns a list of absolute URLs."""
    url = SEARCH_URLS[retailer].format(q=quote_plus(keyword))
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.raise_for_status()
    except Exception as e:
        print(f"Search failed for {retailer} / '{keyword}': {e}")
        return []

    links = set(PRODUCT_LINK_PATTERNS[retailer].findall(r.text))
    if not links:
        print(f"No product links matched for {retailer} / '{keyword}' (page loaded fine, but 0 matches - selector may need updating)")
        return []
    full_links = [BASE_URLS[retailer] + link if link.startswith("/") else link for link in links]
    return full_links[:10]  # cap per keyword/retailer so this stays fast


def check_product_page(url: str):
    """Fetch a product page and return (in_stock: bool|None, price: float|None)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        text = r.text.lower()
    except Exception as e:
        print(f"Product page check failed for {url}: {e}")
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


def expected_price_for(keyword: str, expected_prices: dict):
    for k, v in expected_prices.items():
        if k.lower() in keyword.lower():
            return v
    return None


def main():
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})

    keywords = config.get("keywords", [])
    retailers = config.get("retailers", [])
    expected_prices = config.get("expected_prices", {})
    deal_threshold = config.get("deal_threshold_percent", 15)
    near_location = config.get("near_location", "")

    for retailer in retailers:
        for keyword in keywords:
            urls = discover_products(retailer, keyword)
            for url in urls:
                key = f"{retailer}::{url}"
                prev = state.get(key, {})

                in_stock, price = check_product_page(url)
                if in_stock is None and price is None:
                    continue  # couldn't read this page this run, skip it

                new_entry = {"in_stock": in_stock, "price": price}

                # New stock alert
                if in_stock and prev.get("in_stock") is not True:
                    map_link = maps_link_for(retailer, near_location) if near_location else ""
                    msg = f"IN STOCK ({retailer.title()}): {keyword}\n{url}"
                    if map_link:
                        msg += f"\nNearby stores: {map_link}"
                    send_discord(msg)

                # Deal alert
                expected = expected_price_for(keyword, expected_prices)
                if price and expected:
                    discount_pct = (1 - (price / expected)) * 100
                    already_alerted_deal = prev.get("deal_alerted_price") == price
                    if discount_pct >= deal_threshold and not already_alerted_deal:
                        send_discord(
                            f"DEAL ({retailer.title()}): {keyword} at ${price:.2f} "
                            f"(normally ~${expected:.2f}, {discount_pct:.0f}% off)\n{url}"
                        )
                        new_entry["deal_alerted_price"] = price

                state[key] = new_entry

    save_state(state)


if __name__ == "__main__":
    sys.exit(main())