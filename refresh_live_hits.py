"""Refresh the map's live-hit window from the latest verified stock state.

This is intentionally separate from Discord alerting: a product can remain
verified in stock for many polling cycles without sending duplicate Discord
messages, while the map should continue to show it as a live hit.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "search_config.json"
ALERTS_FILE = ROOT / "docs/alerts.json"
BIG4 = {"target", "walmart", "bestbuy", "gamestop"}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as exc:
        print(f"JSON load failed {path}: {exc}")
        return default


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def main():
    state = load(STATE_FILE, {})
    alerts = load(ALERTS_FILE, [])
    config = load(CONFIG_FILE, {})
    ttl_minutes = max(5, int(config.get("alert_ttl_minutes", 30)))
    now = datetime.now(timezone.utc)
    freshness_cutoff = now - timedelta(minutes=ttl_minutes)

    live_keys = set()
    refreshed = 0

    for key, entry in state.items():
        if key == "schema_version" or not isinstance(entry, dict) or "::" not in key:
            continue
        retailer, url = key.split("::", 1)
        if retailer not in BIG4 or entry.get("in_stock") is not True:
            continue
        last_seen = parse_time(entry.get("last_seen", ""))
        if not last_seen or last_seen < freshness_cutoff:
            continue

        live_keys.add(key)
        existing = next((a for a in alerts if a.get("kind") == "stock" and a.get("retailer") == retailer and a.get("url") == url), None)
        if existing is None:
            alerts.insert(0, {
                "ts": now.isoformat(),
                "detected_at": now.isoformat(),
                "posted_at": entry.get("posted_at"),
                "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
                "kind": "stock",
                "retailer": retailer,
                "title": entry.get("title") or f"{retailer.title()} Pokémon product",
                "url": url,
                "verified": True,
                "stock": True,
                "online": True,
                "stores": [],
            })
        else:
            existing.update({
                "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
                "verified": True,
                "stock": True,
                "online": True,
                "title": entry.get("title") or existing.get("title"),
                "posted_at": entry.get("posted_at"),
                "detected_at": existing.get("detected_at") or now.isoformat(),
                "ts": existing.get("ts") or existing.get("detected_at") or now.isoformat(),
            })
        refreshed += 1

    # A stock hit is no longer live when the latest verified state is not True
    # or when polling has gone stale. Unknown/OOS states remain in state.json
    # but are not displayed as live map hits.
    filtered = []
    for alert in alerts:
        if alert.get("kind") == "stock" and alert.get("retailer") in BIG4:
            key = f"{alert.get('retailer')}::{alert.get('url')}"
            if key not in live_keys:
                continue
        filtered.append(alert)

    save(ALERTS_FILE, filtered[:100])
    print(f"Live-hit map refreshed: {refreshed} current verified Big 4 hit(s)")


if __name__ == "__main__":
    main()
