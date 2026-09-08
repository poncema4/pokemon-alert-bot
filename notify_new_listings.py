"""Discord alerts for newly discovered Big 4 product listings.

New listings are tracked in state.json, but Discord only receives a new-listing
notification when the retailer page verifies the item is in stock. Blocked or
unknown discovery results never become notification spam.
"""
from __future__ import annotations

import json
import os
import subprocess
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


def previous_state():
    try:
        raw = subprocess.check_output(["git", "show", "HEAD:state.json"], text=True)
        return json.loads(raw)
    except Exception:
        return {}


def format_et(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ZoneInfo:
            return dt.astimezone(ZoneInfo("America/New_York")).strftime("%B %-d, %Y %-I:%M %p %Z")
        return dt.astimezone(timezone.utc).strftime("%B %-d, %Y %-I:%M %p UTC")
    except Exception:
        return value


def main():
    current = load(STATE_FILE, {})
    previous = previous_state()
    sent = 0

    for key, entry in current.items():
        if key == "schema_version" or not isinstance(entry, dict) or "::" not in key:
            continue
        retailer, url = key.split("::", 1)
        if retailer not in BIG4 or key in previous or entry.get("pokemon") is not True:
            continue
        if entry.get("in_stock") is not True:
            continue

        detected = entry.get("last_seen") or datetime.now(timezone.utc).isoformat()
        posted = entry.get("posted_at")
        title = entry.get("title") or f"{retailer.title()} Pokémon product"
        lines = [
            f"**{title}**",
            "New Pokémon product listing with verified stock.",
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

    print(f"New verified Big 4 listing alerts sent: {sent}")


if __name__ == "__main__":
    main()
