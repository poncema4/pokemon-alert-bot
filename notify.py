"""Discord webhook alerts."""

import os

import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def alert(title: str, body: str, url: str = "", ping: bool = False):
    if not DISCORD_WEBHOOK_URL:
        print("Discord: no DISCORD_WEBHOOK_URL secret, skip")
        return
    content = f"**{title}**\n{body}"
    if url:
        content += f"\n{url}"
    if ping:
        content = f"@everyone {content}"
    payload = {
        "content": content[:1900],
        "allowed_mentions": {"parse": ["everyone"] if ping else []},
    }
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"Discord: HTTP {r.status_code}")
    except Exception as e:
        print(f"Discord send failed: {e}")
