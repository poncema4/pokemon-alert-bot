"""Read a few retailer pages with the watcher's own browser reader and print exactly what it saw (and how long it took).

Used by the reader-probe workflow to answer "why does the watcher say Best Buy is unreadable?" from a GitHub runner, where the logs of the
running watcher cannot be read. It only loads pages the way a visitor does; a wall is reported as a wall and never worked around.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.browser_reader import BrowserReader, classify_rendered  # noqa: E402

PAGES = {
    "bestbuy_chaos_rising_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-chaos-rising-elite-trainer-box/JJG2TL34RT",
    "bestbuy_perfect_order_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-perfect-order-elite-trainer-box/JJG2TL3W86",
    "bestbuy_30th_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ",
    "bestbuy_pitch_black_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45",
}


STRATEGIES = [("commit", 20000)] * 3  # repeats: hydration timing varies between loads


SELLER = __import__("re").compile(r"(sold (?:and shipped )?by[^\n]{0,80}|shipped by[^\n]{0,60}|marketplace[^\n]{0,60}|other sellers?[^\n]{0,60}|from other sellers[^\n]{0,60})", __import__("re").I)


def seller_lines(reader, url):
    """The wording a visitor sees about WHO sells the item (first party or a marketplace seller), for working out how to tell them apart."""
    page = reader._context.new_page()
    try:
        page.goto(url, wait_until="commit", timeout=25000)
        try:
            page.wait_for_function(reader.READY, timeout=20000)
        except Exception:
            pass
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        found = []
        for m in SELLER.finditer(text):
            context = " | ".join(x.strip() for x in text[max(0, m.start() - 60): m.end() + 140].splitlines() if x.strip())
            if context not in found:
                found.append(context[:260])
        near_button = []
        i = text.lower().find("add to cart")
        if i >= 0:
            near_button = [x.strip() for x in text[max(0, i - 400): i + 120].splitlines() if x.strip()][-14:]
        return {"chars": len(text), "seller_lines": found[:5], "near_add_to_cart": near_button, "has_add_to_cart": "add to cart" in text.lower()}
    finally:
        page.close()


def main() -> int:
    for wait_until, wait_ms in STRATEGIES:
        reader = BrowserReader(wait_ms=wait_ms, wait_until=wait_until)
        try:
            for name, url in PAGES.items():
                started = time.time()
                row = reader.snapshot(url)
                row.pop("url", None)
                stock, reason, signal = classify_rendered(row)
                print("SELLER " + name + " " + json.dumps(seller_lines(reader, url)))
                print(json.dumps({"strategy": f"{wait_until}+{wait_ms}", "page": name, "seconds": round(time.time() - started, 1), "stock": stock, "reason": reason, "signal": signal, "status": row.get("status"), "chars": row.get("body_chars"), "buttons": row.get("buttons"), "error": row.get("error"), "sku": row.get("sku"), "sku_ld": row.get("sku_ld"), "sku_text": row.get("sku_text")}))
        finally:
            reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
