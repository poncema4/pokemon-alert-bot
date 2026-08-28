"""Discord webhook alerts."""

import os

import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

SEPARATOR = "\n\n────────────────────────────────────────\n\n"
MAX_DISCORD_CONTENT = 1900


def _alerts(content: str):
    """Return complete alert blocks, one product alert per Discord message."""
    parts = [p.strip() for p in content.split(SEPARATOR) if p.strip()]
    return parts or [content.strip()]


def alert(title: str, body: str, url: str = "", ping: bool = False):
    if not DISCORD_WEBHOOK_URL:
        print("Discord: no DISCORD_WEBHOOK_URL secret, skip")
        return

    content = f"**{title}**\n{body}"
    if url:
        content += f"\n{url}"

    # Never combine multiple product alerts into one Discord message. This
    # keeps every Product URL, timestamp, and separator intact and makes each
    # notification independently readable/clickable.
    alerts = _alerts(content)
    for index, item in enumerate(alerts, start=1):
        if len(item) > MAX_DISCORD_CONTENT:
            # Keep the alert intact whenever possible; only truncate an
            # unexpectedly oversized individual alert as a last resort.
            item = item[:MAX_DISCORD_CONTENT]
            print(f"Discord: warning — individual alert {index} exceeded {MAX_DISCORD_CONTENT} chars")
        chunk = item
        if ping:
            chunk = f"@everyone {chunk}"
        payload = {
            "content": chunk,
            "allowed_mentions": {"parse": ["everyone"] if ping else []},
        }
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            print(f"Discord: HTTP {r.status_code} (alert {index}/{len(alerts)})")
        except Exception as e:
            print(f"Discord send failed: {e}")
