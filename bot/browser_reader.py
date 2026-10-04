"""Read a retailer page the way a visitor does: in a real headless browser, by its visible buy button.

Best Buy answers a plain HTTP request with a timeout from GitHub's runner but renders fine in a real browser, where the page
shows either an enabled "Add to cart" button or a disabled "Unavailable" / "Sold Out" one. This module reads that button.

It never gets around anything: a captcha, a "press & hold", a queue or an access-denied page is reported as blocked
(unknown) and the page is left alone. The decision is a pure function (`classify_rendered`) so it is tested on real captured pages.
"""
from __future__ import annotations

import re

WALLS = (r"px-captcha", r"press\s*&\s*hold", r"access denied", r"queue-it", r"you are in line", r"just a moment",
         r"verify you are (a )?human", r"robot or human", r"pardon our interruption", r"attention required")
BUY = re.compile(r"^(add to (cart|bag|basket))$", re.I)
NOT_AVAILABLE = re.compile(r"^(sold out|unavailable|currently unavailable|out of stock|coming soon|notify me|check stores|find in store|see details)$", re.I)


def classify_rendered(snapshot):
    """(stock, reason, signal) from {"walls": [...], "buttons": [{"label", "visible", "enabled"}], "body_chars": n}."""
    if snapshot.get("error"):
        return None, "error", None
    if snapshot.get("walls") or any(re.search(w, snapshot.get("title") or "", re.I) for w in WALLS):
        return None, "blocked", None
    buttons = [b for b in snapshot.get("buttons", snapshot.get("buy_buttons", [])) if b.get("visible")]  # (older snapshots call the field buy_buttons)
    if any(BUY.match(b["label"].strip()) and b.get("enabled") for b in buttons):
        return True, "ok", "browser"
    if any(NOT_AVAILABLE.match(b["label"].strip()) or (BUY.match(b["label"].strip()) and not b.get("enabled")) for b in buttons):
        return False, "ok", None
    if (snapshot.get("body_chars") or 0) < 500:
        return None, "blocked", None  # an almost empty page is a block page, not a product page
    return None, "no_signal", None


class BrowserReader:
    """One headless Chromium kept open across cycles (starting it costs seconds, reading a page costs a few)."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", timezone_id="America/New_York")

    def snapshot(self, url, wait_ms=4500):
        page = self._context.new_page()
        row = {"url": url}
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(wait_ms)
            row["status"] = response.status if response else None
            row["title"] = page.title()[:160]
            text = page.inner_text("body") if page.query_selector("body") else ""
            row["body_chars"] = len(text)
            haystack = text + page.content()[:400000]
            row["walls"] = [w for w in WALLS if re.search(w, haystack, re.I)]
            row["buttons"] = []
            for el in page.query_selector_all("button, a[role=button]")[:400]:
                label = (el.inner_text() or "").strip().replace("\n", " ")[:50]
                if label and (BUY.match(label) or NOT_AVAILABLE.match(label)):
                    row["buttons"].append({"label": label, "visible": el.is_visible(), "enabled": el.is_enabled()})
            price = re.search(r"\$\s?([0-9]{1,4}(?:,[0-9]{3})*\.[0-9]{2})", text)
            row["price"] = float(price.group(1).replace(",", "")) if price else None
        except Exception as exc:
            row["error"] = str(exc)[:200]
        finally:
            page.close()
        return row

    def check(self, url):
        """Same shape as monitor.check_product_page."""
        row = self.snapshot(url)
        stock, reason, signal = classify_rendered(row)
        return {"stock": stock, "title": row.get("title") or "", "posted_at": None, "http_status": row.get("status"), "reason": reason, "signal": signal, "price": row.get("price")}

    def close(self):
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass
