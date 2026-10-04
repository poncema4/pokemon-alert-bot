"""Who is selling it: the store itself, or a third-party marketplace seller?

A Best Buy page for a sold-out box still shows an enabled "Add to cart" when a marketplace reseller lists it ("Sold & shipped by Shopville Inc",
"More options from Marketplace sellers $96.99 - $155.94"). That is not Best Buy restocking: it is a reseller at about twice the price, and a
"Best Buy is in stock" ping for it is a false alarm. A reading counts as in stock only when the seller is the store itself or the page names
no seller at all (so a first-party restock is never missed just because a page does not print a seller line).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

STORE_NAMES = {
    "bestbuy.com": ("best buy", "bestbuy"),
    "target.com": ("target",),
    "walmart.com": ("walmart",),
    "gamestop.com": ("gamestop",),
}
# JSON-LD: "seller":{"@type":"Organization","name":"GameStop"}
JSON_SELLER = re.compile(r'"seller"\s*:\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"', re.I)


def store_names(url):
    host = (urlparse(url or "").netloc or "").lower()
    for domain, names in STORE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return names
    return None


def is_store_seller(url, seller):
    """True when the seller is the store itself, when no seller is named, or when the store is not one we know."""
    names = store_names(url)
    if not seller or not names:
        return True
    return any(n in seller.lower() for n in names)


def marketplace_only(url, sellers):
    """True when the page names sellers and none of them is the store itself."""
    sellers = [s for s in sellers if s]
    return bool(sellers) and not any(is_store_seller(url, s) for s in sellers)


def json_sellers(text):
    return JSON_SELLER.findall(text or "")
