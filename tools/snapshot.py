"""Save the retailer pages exactly as the bot sees them (run from GitHub Actions: `snapshot.yml`).

GitHub's runners and a laptop are treated differently by retailers, so false positives and negatives can only be
studied from what the runner really receives. Pages land in snapshots/ together with a one-line summary of the
availability signals found in each, and the workflow uploads them as an artifact.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import monitor  # noqa: E402

OUT = ROOT / "snapshots"
SIGNALS = (r'"availability"\s*:\s*"[^"]+"', r"InStock|OutOfStock|SoldOut|LimitedAvailability|PreOrder|BackOrder", r"Add to Cart|Add to cart|Not Available|Sold Out|Out of Stock|Currently unavailable|Notify Me|Check Availability|Pick up|Ship it|Ship to")


def main():
    config = json.loads((ROOT / "search_config.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    http = requests.Session()
    http.headers.update(monitor.HEADERS)
    index = []
    for retailer, urls in config.get("seed_urls", {}).items():
        for n, url in enumerate(urls):
            name = f"{retailer}_{n}"
            try:
                response = http.get(url, timeout=20, allow_redirects=True)
                (OUT / f"{name}.html").write_text(response.text, encoding="utf-8")
                stock, reason, signal = monitor.classify_response(response.status_code, response.url, response.text)
                found = {}
                for pattern in SIGNALS:
                    for match in re.findall(pattern, response.text):
                        found[match] = found.get(match, 0) + 1
                row = {"file": name, "url": url, "status": response.status_code, "final_url": response.url, "bytes": len(response.text), "bot_says": [stock, reason, signal], "signals": found}
            except Exception as exc:
                row = {"file": name, "url": url, "error": str(exc)[:200]}
            index.append(row)
            print(json.dumps(row)[:600])
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
