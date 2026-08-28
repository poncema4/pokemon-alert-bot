"""Discord webhook alerts."""

import os

import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

SEPARATOR = "\n\n────────────────────────────────────────\n\n"
MAX_DISCORD_CONTENT = 1900


def _chunks(content: str):
    """Split on alert separators so one product alert is never truncated."""
    parts = [p.strip() for p in content.split(SEPARATOR) if p.strip()]
    if not parts:
        return [content[:MAX_DISCORD_CONTENT]]

    chunks = []
    current = ""
    for part in parts:
        candidate = part if not current else current + SEPARATOR + part
        if len(candidate) <= MAX_DISCORD_CONTENT:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(part) > MAX_DISCORD_CONTENT:
            chunks.append(part[:MAX_DISCORD_CONTENT])
            part = part[MAX_DISCORD_CONTENT:]
        current = part
    if current:
        chunks.append(current)
    return chunks


def alert(title: str, body: str, url: str = "", ping: bool = False):
    if not DISCORD_WEBHOOK_URL:
        print("Discord: no DISCORD_WEBHOOK_URL secret, skip")
        return

    content = f"**{title}**\n{body}"
    if url:
        content += f"\n{url}"

    chunks = _chunks(content)
    for index, chunk in enumerate(chunks):
        if index:
            chunk = "────────────────────────────────────────\n\n" + chunk
        if ping:
            chunk = f"@everyone {chunk}"
        payload = {
            "content": chunk[:MAX_DISCORD_CONTENT],
            "allowed_mentions": {"parse": ["everyone"] if ping else []},
        }
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            print(f"Discord: HTTP {r.status_code}")
        except Exception as e:
            print(f"Discord send failed: {e}")
