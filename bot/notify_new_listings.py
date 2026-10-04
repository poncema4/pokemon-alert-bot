"""Discord alerts for newly discovered Big 4 product listings.

New listings are tracked in state.json, but Discord only receives a new-listing
notification when the retailer page verifies the item is in stock. Blocked or
unknown discovery results never become notification spam.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import advisor
from notify import alert, send_card

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 in Actions always has zoneinfo
    ZoneInfo = None

ROOT = Path(__file__).resolve().parents[1]  # repo root (this file lives in bot/)
STATE_FILE = ROOT / "data" / "state.json"
CONFIG_FILE = ROOT / "config" / "search_config.json"
MARKET_FILE = ROOT / "docs/market.json"
BIG4 = {"target", "walmart", "bestbuy", "gamestop"}
MAX_NEW_PER_RUN = 5  # a burst of "new listings" (a retailer that suddenly becomes readable) must never flood the channel


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def format_et(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ZoneInfo:
            return dt.astimezone(ZoneInfo("America/New_York")).strftime("%B %-d, %Y %-I:%M %p %Z")
        return dt.astimezone(timezone.utc).strftime("%B %-d, %Y %-I:%M %p UTC")
    except Exception:
        return value


def main():
    """Announce each newly discovered listing exactly once, and only if its first reading was in stock.

    The "already announced" flag lives in state.json itself (monitor.py writes new_announced=False for a
    brand-new listing), so it does not depend on git history and survives batched commits.
    """
    current = load(STATE_FILE, {})
    market_cache = load(MARKET_FILE, {})
    sent = 0
    overflow = 0
    changed = False

    for key, entry in current.items():
        if key == "schema_version" or not isinstance(entry, dict) or "::" not in key:
            continue
        if entry.get("new_announced", True):
            continue
        entry["new_announced"] = True  # decided now, whatever the outcome: a later restock is a normal stock alert
        changed = True
        retailer, url = key.split("::", 1)
        if retailer not in BIG4 or entry.get("pokemon") is not True:
            continue
        if entry.get("in_stock") is not True:
            continue

        if sent >= MAX_NEW_PER_RUN:
            overflow += 1
            continue
        detected = entry.get("last_seen") or datetime.now(timezone.utc).isoformat()
        title = entry.get("title") or f"{retailer.title()} Pokémon product"
        config = load(CONFIG_FILE, {})
        market = advisor.market_for(config, market_cache, title, url)
        send_card(advisor.build_card(retailer, "new", title, url, config.get("map_url", ""), detected, entry.get("signal"), entry.get("price"), entry.get("msrp"), market,
                                     confirmed=bool(entry.get("confirmed")), ping=os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes")))
        sent += 1

    if overflow:
        notice = f"{overflow} more new in-stock listings were found in this run (the first {MAX_NEW_PER_RUN} are above). They are on the map; check it for the rest."
        alert("🆕 MORE NEW LISTINGS", notice, tone="fair")
    if changed:
        STATE_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        MARKET_FILE.parent.mkdir(parents=True, exist_ok=True)
        MARKET_FILE.write_text(json.dumps(market_cache, indent=2) + "\n", encoding="utf-8")
    print(f"New verified Big 4 listing alerts sent: {sent}")


if __name__ == "__main__":
    main()
