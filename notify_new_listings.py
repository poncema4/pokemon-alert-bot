"""Send Discord alerts for newly discovered Big 4 product listings.

The main monitor handles verified restocks. This companion step handles the
important case where a retailer page is newly discovered but stock is unknown
or currently unavailable: it still alerts once so a fast manual refresh can
catch a fleeting drop.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from notify import alert

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
MAP_URL = "https://poncema4.github.io/pokemon-alert-bot/"
BIG4 = {"target", "walmart", "bestbuy", "gamestop"}
SEPARATOR = "\n\n────────────────────────────────────────\n"


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
        # Eastern Daylight Time applies on August 27, 2026.
        return dt.astimezone(timezone(timedelta(hours=-4))).strftime("%B %-d, %Y %-I:%M %p EDT")
    except Exception:
        return value


def status(entry):
    stock = entry.get("in_stock")
    if stock is True:
        return "IN STOCK"
    if stock is False:
        return "OUT OF STOCK"
    return "AVAILABILITY UNKNOWN"


def main():
    current = load(STATE_FILE, {})
    previous = previous_state()
    sent = 0

    for key, entry in current.items():
        if key == "schema_version" or not isinstance(entry, dict) or "::" not in key:
            continue
        retailer, url = key.split("::", 1)
        if retailer not in BIG4 or key in previous:
            continue
        if entry.get("pokemon") is not True:
            continue

        detected = entry.get("last_seen") or datetime.now(timezone.utc).isoformat()
        posted = entry.get("posted_at")
        title = entry.get("title") or f"{retailer.title()} Pokémon product"
        body = [
            f"**{title}**",
            f"Status: **{status(entry)}**",
            "New Pokémon product listing detected on the retailer site.",
            "Stock can change quickly, so refresh the product page before assuming it is unavailable.",
        ]
        if posted:
            body.append(f"Time Posted: {format_et(posted)}")
        else:
            body.append("Time Posted: Not published by the retailer.")
        body.append(f"Detected: {format_et(detected)}")
        body.append(f"Map: {MAP_URL}")
        body.append(f"Product: {url}")
        alert(f"NEW LISTING — {retailer.title()}", "\n".join(body), ping=True)
        sent += 1

    print(f"New Big 4 listing alerts sent: {sent}")


if __name__ == "__main__":
    main()
