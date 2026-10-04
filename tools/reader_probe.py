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
    "bestbuy_30th_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ",
    "bestbuy_pitch_black_etb": "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45",
}


STRATEGIES = [("commit", 12000), ("domcontentloaded", 12000), ("load", 12000), ("load", 25000)]


def main() -> int:
    for wait_until, wait_ms in STRATEGIES:
        reader = BrowserReader(wait_ms=wait_ms, wait_until=wait_until)
        try:
            for name, url in PAGES.items():
                started = time.time()
                row = reader.snapshot(url)
                row.pop("url", None)
                stock, reason, signal = classify_rendered(row)
                print(json.dumps({"strategy": f"{wait_until}+{wait_ms}", "page": name, "seconds": round(time.time() - started, 1), "stock": stock, "reason": reason, "signal": signal, "status": row.get("status"), "chars": row.get("body_chars"), "buttons": row.get("buttons"), "error": row.get("error")}))
        finally:
            reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
