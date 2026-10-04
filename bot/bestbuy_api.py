"""Best Buy's official Products API: real availability for Best Buy's own stock, with no browser and no bot wall.

A free key comes from developer.bestbuy.com (sign up with an email address, activate the key from the email). It is used only when the
BESTBUY_API_KEY environment variable (a GitHub secret) is set; without it the watcher falls back to reading pages in a browser.

Strictness: a product is IN STOCK only when the API says it can be ordered online AND its ordering status is exactly "Available". "Sold out" and
"coming soon" style statuses are out of stock. Anything else (a status we do not recognise, a missing field, an error) is UNKNOWN, which never alerts.
The Products API describes Best Buy's own catalogue, so a marketplace reseller's offer does not make a product look available here.

The key is part of the request URL, so errors are reported without the URL (requests puts it in its messages): it must never reach a log.
"""
from __future__ import annotations

import re

ENDPOINT = "https://api.bestbuy.com/v1/products"
FIELDS = "sku,name,salePrice,regularPrice,onlineAvailability,orderable,inStoreAvailability,addToCartUrl,url"
SKU = re.compile(r"\d{7}")
OUT_OF_STOCK = {"soldout", "sold out", "notorderable", "not orderable", "comingsoon", "coming soon", "notavailable", "not available", "unavailable"}


def reading(product):
    """One API product -> the same shape the page readers return."""
    online = product.get("onlineAvailability")
    status = str(product.get("orderable") or "").strip()
    if online is True and status.lower() == "available":
        stock, reason, signal = True, "ok", "api"
    elif online is False or status.lower() in OUT_OF_STOCK:
        stock, reason, signal = False, "ok", None
    else:
        stock, reason, signal = None, "no_signal", None
    sku = str(product.get("sku") or "")
    return {"stock": stock, "title": product.get("name") or "", "posted_at": None, "http_status": 200, "reason": reason, "signal": signal,
            "price": product.get("salePrice"), "sku": sku if SKU.fullmatch(sku) else None, "seller": None, "add_url": product.get("addToCartUrl")}


def fetch(skus, key, http, timeout=8):
    """{sku: reading} for the given 7-digit SKUs in ONE request, or {} when the API cannot be used (no key, bad status, bad JSON)."""
    skus = sorted({str(s) for s in skus if SKU.fullmatch(str(s))})
    if not key or not skus:
        return {}
    url = f"{ENDPOINT}(sku in({','.join(skus)}))?apiKey={key}&show={FIELDS}&pageSize=100&format=json"
    try:
        response = http.get(url, timeout=timeout)
    except Exception as exc:
        print(f"  Best Buy API unavailable ({type(exc).__name__}); falling back to the browser")
        return {}
    if response.status_code != 200:
        print(f"  Best Buy API answered HTTP {response.status_code}; falling back to the browser")
        return {}
    try:
        products = response.json().get("products", [])
    except Exception:
        print("  Best Buy API answered something that is not JSON; falling back to the browser")
        return {}
    found = {}
    for product in products:
        r = reading(product)
        if r["sku"]:
            found[r["sku"]] = r
    return found


def search(words, key, http, timeout=10):
    """Products whose name matches every word (for finding SKUs): [{"sku", "name", "stock", "price"}], or [] when the API cannot be used."""
    words = [w for w in (str(x).strip() for x in words) if re.fullmatch(r"[A-Za-z0-9\u00C0-\u017F'-]+", w)]
    if not key or not words:
        return []
    query = "&".join(f"search={w}" for w in words)
    try:
        response = http.get(f"{ENDPOINT}({query})?apiKey={key}&show={FIELDS}&pageSize=25&format=json", timeout=timeout)
        if response.status_code != 200:
            print(f"  Best Buy API answered HTTP {response.status_code}")
            return []
        products = response.json().get("products", [])
    except Exception as exc:
        print(f"  Best Buy API search failed ({type(exc).__name__})")
        return []
    out = []
    for product in products:
        r = reading(product)
        if r["sku"]:
            out.append({"sku": r["sku"], "name": r["title"], "stock": r["stock"], "price": r["price"]})
    return out
