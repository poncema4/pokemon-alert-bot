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

from notify import alert

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.11 in Actions always has zoneinfo
    ZoneInfo = None

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
BIG4 = {"target", "walmart", "bestbuy", "gamestop"}


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
    sent = 0
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

        detected = entry.get("last_seen") or datetime.now(timezone.utc).isoformat()
        posted = entry.get("posted_at")
        title = entry.get("title") or f"{retailer.title()} Pokémon product"
        lines = [
            f"**{title}**",
            "New Pokémon product listing with verified stock."
            if entry.get("signal") != "text"
            else "New Pokémon product listing, likely in stock (cart wording only, no structured data). Confirm on the page.",
            f"Detected: {format_et(detected)}",
            "Map: [Open map](https://poncema4.github.io/pokemon-alert-bot/)",
            f"Product: [Open product page]({url})",
        ]
        if posted:
            lines.insert(2, f"Time Posted: {format_et(posted)}")

        alert(
            f"🆕🟢 NEW + IN STOCK — {retailer.title()}",
            "\n".join(lines),
            ping=os.environ.get("DISCORD_PING", "").lower() in ("1", "true", "yes"),
        )
        sent += 1

    if changed:
        STATE_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"New verified Big 4 listing alerts sent: {sent}")


if __name__ == "__main__":
    main()
