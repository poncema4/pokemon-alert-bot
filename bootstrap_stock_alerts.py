"""Make verified-but-never-alerted stock eligible for one alert.

The monitor historically allowed an item to be written into state as IN STOCK
before the alert path saw it. That meant an item could remain IN STOCK forever
without a first Discord notification. This one-time-safe normalization changes
only verified stock entries that have never had a stock alert into a transition
state. Once monitor.py sends the alert, last_stock_alert is populated and the
entry is untouched on later runs.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"


def main():
    data = json.loads(STATE.read_text(encoding="utf-8"))
    changed = 0
    for key, entry in data.items():
        if key == "schema_version" or not isinstance(entry, dict):
            continue
        if entry.get("in_stock") is True and not entry.get("last_stock_alert"):
            entry["in_stock"] = False
            changed += 1
    STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Bootstrap normalized {changed} verified-but-never-alerted stock entries.")


if __name__ == "__main__":
    main()
