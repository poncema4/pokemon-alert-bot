"""Load retailer pages in a real headless browser, exactly as a visitor would, and report what is visible.

Why: Target and Pokémon Center fill in their stock with JavaScript, so a plain HTTP fetch sees only a placeholder or a block page.
A browser shows what a visitor sees. If a page puts up a captcha, a queue or an access-denied screen, this tool reports that and
stops; it never tries to solve or get around one.

Output: snapshots/browser/<name>.png and snapshots/browser/index.json (uploaded by the browser-snapshot workflow).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "snapshots" / "browser"
PAGES = {
    "target_30th_etb": "https://www.target.com/p/-/A-1010892076",
    "bestbuy_30th_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ",
    "bestbuy_pitch_black_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45",
    "walmart_30th_etb": "https://www.walmart.com/ip/20754418655",
    "gamestop_pitch_black_etb": "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html",
    "pokemoncenter_home": "https://www.pokemoncenter.com/",
    "pokemoncenter_tcg": "https://www.pokemoncenter.com/category/trading-card-games",
}
WALLS = (r"px-captcha", r"press\s*&\s*hold", r"access denied", r"queue-it", r"you are in line", r"just a moment", r"verify you are (a )?human", r"robot or human", r"pardon our interruption")
LABELS = re.compile(r"^(add to cart|add to bag|sold out|out of stock|currently unavailable|unavailable|notify me|coming soon|pre-?order|check stores|see details|find in store|ship it|pick up|deliver it)", re.I)


def main():
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US", timezone_id="America/New_York")
        for name, url in PAGES.items():
            row = {"name": name, "url": url}
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(9000)  # let the page fill in its own stock state
                row["status"] = response.status if response else None
                row["final_url"] = page.url
                row["title"] = page.title()[:120]
                text = page.inner_text("body")[:200000] if page.query_selector("body") else ""
                row["body_chars"] = len(text)
                row["walls"] = [w for w in WALLS if re.search(w, text, re.I) or re.search(w, page.content()[:400000], re.I)]
                buttons = []
                for el in page.query_selector_all("button, a[role=button], input[type=submit]")[:400]:
                    label = (el.inner_text() or el.get_attribute("value") or "").strip().replace("\n", " ")[:50]
                    if label and LABELS.match(label):
                        buttons.append({"label": label, "visible": el.is_visible(), "enabled": el.is_enabled()})
                row["buy_buttons"] = buttons[:12]
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
            except Exception as exc:
                row["error"] = str(exc)[:200]
            finally:
                page.close()
            report.append(row)
            print(json.dumps(row)[:700])
        browser.close()
    (OUT / "index.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
