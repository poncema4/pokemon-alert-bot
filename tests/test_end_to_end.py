"""End to end: a restock goes through the REAL watcher code, out to a (local) Discord webhook, and onto the REAL website in a real browser.

What is real here: monitor.main (reading, confirming, once-per-stay rule, state), notify (the Discord payload), refresh_live_hits (the Live online
list), alerts.json / health.json / state.json on disk, the site's own JavaScript in Chromium.
What is faked: the retailers' answers (so a restock can be staged on demand) and the Discord server (a local HTTP server that records every POST).

It proves, for Target, Walmart, Best Buy and GameStop (the four stores the bot can read):
  1. a restock sends exactly ONE Discord message and shows on the site's Live online section, and the two agree (title, price, link),
  2. further readings of the same stay send nothing more, even through a blocked / unknown reading in the middle,
  3. selling out clears Live online and sends nothing,
and that Pokémon Center (which blocks bots) never produces an alert and is shown honestly as "can't read".

Run: python tests/test_end_to_end.py   (needs Playwright + Chromium; CI requires it)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "tests"))

import advisor  # noqa: E402
import monitor  # noqa: E402
import notify  # noqa: E402
import refresh_live_hits  # noqa: E402
import test_accuracy as acc  # noqa: E402  (reuses its fake HTTP session and fixtures)

PAGES = {
    "target": ("https://www.target.com/p/-/A-1010892076", "Pokemon TCG Elite Trainer Box (Target)", 59.99),
    "walmart": ("https://www.walmart.com/ip/Pokemon-TCG-Elite-Trainer-Box/15718673510", "Pokemon TCG Elite Trainer Box (Walmart)", 54.99),
    "bestbuy": ("https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45", "Pokemon Pitch Black Elite Trainer Box (Best Buy)", 49.99),
    "gamestop": ("https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html", "Pokemon Trading Card Game: Pitch Black Elite Trainer Box | GameStop", 84.99)   # the real captured page: its own title and price,
}


def structured(title, price, in_stock):
    state = "InStock" if in_stock else "OutOfStock"
    return (f'<html><head><title>{title}</title><script type="application/ld+json">{{"@type":"Product","name":"{title}","offers":'
            f'{{"@type":"Offer","price":"{price}","availability":"https://schema.org/{state}"}}}}</script></head><body>{title}</body></html>')


class Stage:
    """What every retailer currently answers: 'in' (in stock), 'out' (sold out) or 'blocked' (a bot wall)."""
    mode = "out"


class FakeBrowser:
    def check(self, url):
        title, price = PAGES["bestbuy"][1], PAGES["bestbuy"][2]
        if Stage.mode == "blocked":
            return {"stock": None, "title": "Access Denied", "posted_at": None, "http_status": 200, "reason": "blocked", "signal": None, "price": None, "sku": None}
        return {"stock": Stage.mode == "in", "title": title, "posted_at": None, "http_status": 200, "reason": "ok",
                "signal": "browser" if Stage.mode == "in" else None, "price": price, "sku": "6678361"}


def http_answer(retailer):
    title, price = PAGES[retailer][1], PAGES[retailer][2]

    def answer(url):
        if Stage.mode == "blocked":
            return acc._Resp(403, url, "Access denied")
        if retailer == "gamestop":
            page = "gamestop_pitch_black_etb_available.html" if Stage.mode == "in" else "gamestop_30th_etb_unavailable.html"
            return acc._Resp(200, url, (ROOT / "tests" / "fixtures" / "pages" / page).read_text(encoding="utf-8"))
        return acc._Resp(200, url, structured(title, price, Stage.mode == "in"))
    return answer


class Discord(BaseHTTPRequestHandler):
    posts = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Discord.posts.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def serve(handler, **kw):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if os.environ.get("POKEPING_REQUIRE_BROWSER") == "1":
            print("FAIL: CI must have Playwright installed")
            return 1
        print("skipped: Playwright is not installed here")
        return 0

    discord = serve(Discord)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        site = tmp / "docs"
        shutil.copytree(ROOT / "docs", site)
        config = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
        config.update({"retailers": ["target", "walmart", "bestbuy", "gamestop"], "keywords": [], "browser_retailers": ["bestbuy"],
                       "seed_urls": {r: [PAGES[r][0]] for r in PAGES}, "listing_pages": {}})
        (tmp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        state = {"schema_version": 4}
        for r, (url, title, price) in PAGES.items():
            state[f"{r}::{monitor.canonical_url(url)}"] = {"pokemon": True, "title": title, "in_stock": False, "last_seen": old, "first_seen": old, "new_announced": True, "armed": True}
        (tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")

        monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE, monitor.MARKET_FILE, monitor.PIDS_FILE = tmp / "state.json", site / "alerts.json", site / "health.json", site / "market.json", tmp / "pids.json"
        monitor.CONFIG_FILE = tmp / "config.json"
        refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE = tmp / "state.json", site / "alerts.json", tmp / "config.json"
        notify.DISCORD_WEBHOOK_URL = f"http://127.0.0.1:{discord.server_port}/hook"
        os.environ["DISCORD_PING"] = "true"
        monitor.CONFIRM_DELAY = 0
        real = (monitor.requests.Session, monitor.discover_products, monitor.gamestop_discovery.discover, advisor.fetch_query, monitor.BROWSER)
        monitor.requests.Session = lambda: acc._FakeSession({"target.com": http_answer("target"), "walmart.com": http_answer("walmart"), "gamestop.com": http_answer("gamestop")})
        monitor.discover_products = lambda *a, **k: []
        monitor.gamestop_discovery.discover = lambda *a, **k: []
        advisor.fetch_query = acc._fake_tcg_search
        monitor.BROWSER = FakeBrowser()

        def cycle(mode):
            Stage.mode = mode
            monitor.main(discover=False, cycle=0)
            refresh_live_hits.main()

        class Quiet(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(site), **k)

            def log_message(self, *a):
                pass

        web = serve(Quiet)
        failures = []

        def check(ok, what):
            print(("PASS " if ok else "FAIL ") + what)
            if not ok:
                failures.append(what)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    def live_cards():
                        page = browser.new_page()
                        page.goto(f"http://127.0.0.1:{web.server_port}/index.html", wait_until="load", timeout=45000)
                        page.wait_for_selector("#live .hit, #live .empty", timeout=20000)
                        cards = page.evaluate("() => [...document.querySelectorAll('#live .hit')].map(h => ({name: h.querySelector('.name').textContent.trim(), href: h.querySelector('.name').href, price: h.querySelector('.dist').textContent.trim(), where: h.querySelector('.addr').textContent}))")
                        empty = page.evaluate("() => !!document.querySelector('#live .empty')")
                        lamps = page.evaluate("() => [...document.querySelectorAll('.lamp')].map(l => l.textContent.replace(/\\s+/g, ' ').trim())")
                        page.close()
                        return cards, empty, lamps

                    # 1. a restock at all four stores
                    cycle("in")
                    posts = list(Discord.posts)
                    check(len(posts) == 4, f"one Discord message per restocked store (got {len(posts)})")
                    check(all(p["content"] == "@everyone" for p in posts), "each message pings @everyone")
                    embeds = {p["embeds"][0]["title"].split("·")[-1].strip(): p["embeds"][0] for p in posts}
                    check(set(embeds) == {"Target", "Walmart", "Best Buy", "GameStop"}, f"one message for each of the four stores: {sorted(embeds)}")
                    cards, empty, lamps = live_cards()
                    check(len(cards) == 4 and not empty, f"Live online shows all four restocks (got {len(cards)})")
                    for r, (url, title, price) in PAGES.items():
                        label = {"target": "Target", "walmart": "Walmart", "bestbuy": "Best Buy", "gamestop": "GameStop"}[r]
                        embed = embeds.get(label)
                        card = next((c for c in cards if label in c["where"]), None)
                        if not embed or not card:
                            check(False, f"{label}: present in both Discord and Live online")
                            continue
                        links = next(f["value"] for f in embed["fields"] if f["name"] == "Links")
                        canonical = monitor.canonical_url(url)
                        check(f"]({canonical})" in links and card["href"].rstrip("/") == canonical.rstrip("/"), f"{label}: Discord and the site link to the same product page")
                        check(f"${price:.2f}" in next(f["value"] for f in embed["fields"] if f["name"] == "Price") and card["price"] == f"${price:.2f}", f"{label}: Discord and the site show the same price (${price:.2f})")
                        check(title.split(" (")[0].lower() in (embed["description"] + card["name"]).lower() or title.lower() in card["name"].lower(), f"{label}: the product name matches")
                    bb_links = next(f["value"] for f in embeds["Best Buy"]["fields"] if f["name"] == "Links")
                    check("[Add to cart](https://api.bestbuy.com/click/-/6678361/cart)" in bb_links, "Best Buy's alert carries the real Add to cart link (SKU 6678361)")
                    wm_links = next(f["value"] for f in embeds["Walmart"]["fields"] if f["name"] == "Links")
                    check("[Add to cart](https://affil.walmart.com/cart/addToCart?items=15718673510)" in wm_links, "Walmart's alert carries the real Add to cart link")
                    check(not any("[My cart]" in f["value"] for e in embeds.values() for f in e["fields"]), "no useless [My cart] link anywhere")
                    check(not any(t in json.dumps(posts) for t in ("http://", "https://")) or all("](http" in f["value"] or f["name"] != "Links" for e in embeds.values() for f in e["fields"]), "every link is a hyperlink (no raw URL previews)")

                    # 2. the same stay never pings again, even through an unknown reading
                    cycle("in")
                    check(len(Discord.posts) == 4, "a second reading of the same stay sends nothing")
                    cycle("blocked")
                    cycle("in")
                    check(len(Discord.posts) == 4, "in stock -> blocked -> in stock is still the same stay: no second ping")
                    cards, empty, lamps = live_cards()
                    check(len(cards) == 4, "Live online still shows all four")

                    # 3. selling out clears Live online and sends nothing
                    cycle("out")
                    check(len(Discord.posts) == 4, "selling out sends nothing")
                    cards, empty, lamps = live_cards()
                    check(not cards and empty, "after a sell-out, Live online is empty again")
                    cycle("in")
                    check(len(Discord.posts) == 4, "a 30-second sell-out blip is not a new stay: still no second ping")

                    # 4. Pokémon Center is honest
                    pc = next((l for l in lamps if "Pok" in l and "Center" in l), "")
                    check("can't read" in pc, f"Pokémon Center is shown as can't read, never as a source of alerts ({pc!r})")
                    check(not any("Pok" in p["embeds"][0]["title"] and "Center" in p["embeds"][0]["title"] for p in Discord.posts), "no Discord alert ever claims a Pokémon Center restock")

                finally:
                    browser.close()
        finally:
            monitor.requests.Session, monitor.discover_products, monitor.gamestop_discovery.discover, advisor.fetch_query, monitor.BROWSER = real
            web.shutdown()
            discord.shutdown()

    print("\nEND TO END: " + ("all checks passed" if not failures else f"{len(failures)} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
