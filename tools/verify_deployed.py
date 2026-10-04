"""Load the DEPLOYED PokePing site in a real browser and check it works: no script errors, the map and route show Open/Closed/Hours?
for every store, the top bar lamps are there, and the scripts being served are the ones in this repo.

Run by the verify-deployed workflow after every GitHub Pages build (and by hand: python tools/verify_deployed.py [base-url]).
Exit code 1 and a printed reason on any failure.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://poncema4.github.io/pokemon-alert-bot/").rstrip("/") + "/"


def served(path: str) -> bytes:
    req = urllib.request.Request(f"{BASE}{path}?nocache={int(time.time())}", headers={"Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=30).read()


def check_release() -> list[str]:
    problems = []
    for rel in ("route.html", "index.html", "js/common.js", "js/map.js", "stores.json"):
        if served(rel) != (ROOT / "docs" / rel).read_bytes():
            problems.append(f"the deployed {rel} is not the one in the repo (Pages build still running, or stale)")
    return problems


def check_pages() -> list[str]:
    from playwright.sync_api import sync_playwright
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for name in ("index.html", "route.html", "etbs.html", "30th.html"):
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)[:160]))
                page.goto(BASE + name, wait_until="load", timeout=45000)
                page.wait_for_timeout(5000)
                if errors:
                    problems.append(f"{name}: script errors {errors[:3]}")
                if not page.locator(".lamp").count():
                    problems.append(f"{name}: no status lamps in the top bar")
                if name == "index.html":
                    tags = page.evaluate("() => [...document.querySelectorAll('.store .tag')].map(t => t.textContent)")
                    if not tags or any(t not in ("Open", "Closed", "Hours?") for t in tags):
                        problems.append(f"index.html: store cards show no Open/Closed tags: {tags[:5]}")
                if name == "route.html":
                    labels = page.evaluate("() => [...document.querySelectorAll('article.stop .status-text')].map(t => t.textContent)")
                    if not labels or not all(re.match(r"(Open now|Closed|Hours unknown)", t) for t in labels):
                        problems.append(f"route.html: stops show no Open/Closed status: {labels[:5]}")
                page.close()
        finally:
            browser.close()
    return problems


def main() -> int:
    problems = []
    try:
        problems += check_release()
    except Exception as exc:  # network trouble is a failure too, never a silent pass
        problems.append(f"could not fetch the deployed site: {exc}")
    try:
        problems += check_pages()
    except Exception as exc:
        problems.append(f"browser check failed: {exc}")
    for line in problems:
        print("FAIL:", line)
    print("deployed site verified" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
