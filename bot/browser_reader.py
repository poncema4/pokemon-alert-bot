"""Read a retailer page the way a visitor does: in a real headless browser, by its visible buy button.

Best Buy answers a plain HTTP request with a timeout from GitHub's runner but renders fine in a real browser, where the page
shows either an enabled "Add to cart" button or a disabled "Unavailable" / "Sold Out" one. This module reads that button.

It never gets around anything: a captcha, a "press & hold", a queue or an access-denied page is reported as blocked
(unknown) and the page is left alone. The decision is a pure function (`classify_rendered`) so it is tested on real captured pages.
"""
from __future__ import annotations

import re
import time

import sellers

WALLS = (r"px-captcha", r"press\s*&\s*hold", r"access denied", r"queue-it", r"you are in line", r"just a moment",
         r"verify you are (a )?human", r"robot or human", r"pardon our interruption", r"attention required")
BUY = re.compile(r"^(add to (cart|bag|basket))$", re.I)
NOT_AVAILABLE = re.compile(r"^(sold out|unavailable|currently unavailable|out of stock|coming soon|notify me|check stores|find in store|see details)$", re.I)


time_sleep = time.sleep
RETRY_PAUSE = 3.0   # seconds before the single reload of a half-loaded page


def is_wall(snapshot):
    return bool(snapshot.get("walls")) or any(re.search(w, snapshot.get("title") or "", re.I) for w in WALLS)


def classify_rendered(snapshot):
    """(stock, reason, signal) from {"walls": [...], "buttons": [{"label", "visible", "enabled"}], "body_chars": n}."""
    if snapshot.get("error"):
        return None, "error", None
    if is_wall(snapshot):
        return None, "blocked", None
    buttons = [b for b in snapshot.get("buttons", snapshot.get("buy_buttons", [])) if b.get("visible")]  # (older snapshots call the field buy_buttons)
    if any(BUY.match(b["label"].strip()) and b.get("enabled") for b in buttons):
        if sellers.marketplace_only(snapshot.get("url"), [snapshot.get("seller")]):
            return False, "marketplace_only", None  # the button belongs to a third-party seller: the store itself has none
        return True, "ok", "browser"
    if any(NOT_AVAILABLE.match(b["label"].strip()) or (BUY.match(b["label"].strip()) and not b.get("enabled")) for b in buttons):
        return False, "ok", None
    if (snapshot.get("body_chars") or 0) < 500:
        return None, "blocked", None  # an almost empty page is a block page, not a product page
    return None, "no_signal", None


class BrowserReader:
    """One headless Chromium kept open across cycles (starting it costs seconds, reading a page costs a few)."""

    def __init__(self, wait_ms=12000, wait_until="commit"):
        from playwright.sync_api import sync_playwright
        self.wait_ms = wait_ms
        self.wait_until = wait_until
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", timezone_id="America/New_York")
        # Reading a button needs no pictures, fonts or video: skipping them makes heavy store pages load in a fraction of the time.
        self._context.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_())

    # One call into the page collects everything: asking the browser about hundreds of buttons one at a time (three round trips each)
    # made a single Best Buy page take about a minute.
    COLLECT = """() => {
      const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const wanted = /^(add to (cart|bag|basket)|sold out|unavailable|currently unavailable|out of stock|coming soon|notify me|check stores|find in store|see details)$/i;
      const buttons = [];
      for (const el of document.querySelectorAll('button, a[role=button]')) {   // every button is looked at; only buy/unavailable ones are kept
        const label = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50);
        if (label && wanted.test(label)) buttons.push({label, visible: visible(el), enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true'});
        if (buttons.length >= 40) break;
      }
      const skuMatch = /(?:"skuId"\\s*:\\s*"?|data-sku-id="|\\bSKU:?\\s*(?:<[^>]*>\\s*)*)(\\d{7})\\b/.exec(document.documentElement.innerHTML);
      let ldSku = null;   // the page's own product, from its structured data: the most trustworthy SKU
      for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const j = JSON.parse(el.textContent);
          for (const o of (Array.isArray(j) ? j : [j]).flatMap((x) => (x && x['@graph']) || [x])) {
            if (o && o['@type'] === 'Product' && /^\\d{7}$/.test(String(o.sku || ''))) ldSku = String(o.sku);
          }
        } catch (e) {}
      }
      // Who sells it: the line after "Sold & shipped by" that follows the buy button (a marketplace reseller on Best Buy shows its own name here).
      const bodyText = document.body ? document.body.innerText : '';
      const soldBy = /Sold\\s*(?:&|and)\\s*shipped by\\s*\\n+\\s*([^\\n]{1,80})/i;
      const addAt = bodyText.search(/Add to cart/i);
      const sm = (addAt >= 0 ? soldBy.exec(bodyText.slice(addAt)) : null) || soldBy.exec(bodyText);
      return {seller: sm ? sm[1].trim() : null, sku: ldSku || (skuMatch ? skuMatch[1] : null), sku_ld: ldSku, sku_text: skuMatch ? skuMatch[1] : null, title: document.title.slice(0, 160), text: (document.body ? document.body.innerText : '').slice(0, 150000),
              captchaNode: !!document.querySelector('#px-captcha, [id*=captcha], iframe[src*=captcha]'), buttons};
    }"""
    READY = "() => Array.from(document.querySelectorAll('button')).some(b => /^(add to (cart|bag|basket)|sold out|unavailable|coming soon|check stores|notify me)$/i.test((b.innerText||'').trim()))"

    def snapshot(self, url, wait_ms=None):
        wait_ms = self.wait_ms if wait_ms is None else wait_ms
        page = self._context.new_page()
        page.set_default_timeout(15000)
        row = {"url": url}
        try:
            response = page.goto(url, wait_until=self.wait_until, timeout=25000)
            try:  # return the moment a buy/unavailable button exists; a page that never shows one waits the full time
                page.wait_for_function(self.READY, timeout=wait_ms)
            except Exception:
                pass
            row["status"] = response.status if response else None
            data = page.evaluate(self.COLLECT)
            text = data["text"]
            row["title"] = data["title"]
            row["seller"] = data.get("seller")
            row["sku"] = data.get("sku")
            row["sku_ld"], row["sku_text"] = data.get("sku_ld"), data.get("sku_text")
            row["body_chars"] = len(text)
            haystack = text + (" px-captcha" if data["captchaNode"] else "")
            row["walls"] = [w for w in WALLS if re.search(w, haystack, re.I)]
            row["buttons"] = [b for b in data["buttons"] if BUY.match(b["label"]) or NOT_AVAILABLE.match(b["label"])]
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
        # About half of Best Buy's loads from a runner are an empty stub (or a page that has not filled in yet). One reload usually gets the real
        # page. A captcha / robot wall and an error are never reloaded: a wall is respected, and errors are handled by the store back-off.
        if reason in ("blocked", "no_signal") and not row.get("error") and not is_wall(row):
            time_sleep(RETRY_PAUSE)
            row = self.snapshot(url)
            stock, reason, signal = classify_rendered(row)
        return {"stock": stock, "title": row.get("title") or "", "posted_at": None, "http_status": row.get("status"), "reason": reason, "signal": signal, "price": row.get("price"), "sku": row.get("sku"), "seller": row.get("seller")}

    def close(self):
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass
