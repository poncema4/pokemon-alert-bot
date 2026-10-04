"""Discord alerts as clean embeds.

A stock alert is one embed: a coloured card whose title names the retailer, with the product as a bold hyperlink
(so the big product image never unfurls), the price verdict ("BUY: LOW", "ABOVE MARKET"...), the numbers behind it,
the proof that it is really in stock, and links. `@everyone` rides in the message content when DISCORD_PING is on.

Everything that builds a message is a pure function (testable without Discord); only `post` talks to the network.
"""
import os
from datetime import datetime, timezone

import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BOT_NAME = "PokePing"
COLORS = {"good": 0x2BD4A0, "fair": 0xFFD23F, "high": 0xF2A33A, "unknown": 0x8FA1B8, "blind": 0xE4352B, "ok": 0x2BD4A0}
LIMITS = {"title": 256, "description": 4096, "field_name": 256, "field_value": 1024, "footer": 2048, "total": 6000, "fields": 25}
PROOF = {
    "page": "Verified: the retailer's own page says it is available.",
    "structured": "Verified: the page's availability data says in stock.",
    "text": "Likely: cart or pickup wording found, no structured data. Confirm on the page.",
    "browser": "Verified: a real browser saw an enabled Add to cart button on the page.",
}


def clip(text, limit):
    text = str(text if text is not None else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def money(value):
    return "—" if value is None else f"${float(value):,.2f}"


def stock_embed(card):
    """Build the Discord payload for one stock alert. `card` is a plain dict (see monitor.build_card)."""
    retailer = card["retailer"]
    heading = {"new": "🆕 NEW LISTING IN STOCK", "test": "🧪 TEST ALERT"}.get(card.get("kind"), "🟢 IN STOCK")
    verdict = card["verdict"]
    fields = [
        {"name": "Price", "value": money(card.get("price")), "inline": True},
        {"name": "Retail", "value": money(card.get("msrp")) + (f"\n{verdict['retail']}" if verdict.get("retail") else ""), "inline": True},
        {"name": "TCGplayer market", "value": (money(card["market"]["market"]) + f"\n{card['market_age']}") if card.get("market") else "not found", "inline": True},
        {"name": "Proof", "value": PROOF.get(card.get("signal"), PROOF["text"]) + (" Confirmed by a second reading seconds later." if card.get("confirmed") else ""), "inline": False},
        {"name": "Links", "value": f"[Open product]({card['url']})" + (f"  ·  [Cart]({card['cart_url']})" if card.get("cart_url") else "") + f"  ·  [Map]({card['map_url']})" + (f"  ·  [TCGplayer]({card['market_url']})" if card.get("market_url") else ""), "inline": False},
    ]
    embed = {
        "title": clip(f"{heading} · {retailer}", LIMITS["title"]),
        "description": clip(f"**[{card['title']}]({card['url']})**\n**{verdict['label']}** — {verdict['summary']}", LIMITS["description"]),
        "color": COLORS.get(verdict["tone"], COLORS["unknown"]),
        "fields": [{"name": clip(f["name"], LIMITS["field_name"]), "value": clip(f["value"], LIMITS["field_value"]), "inline": f["inline"]} for f in fields][: LIMITS["fields"]],
        "footer": {"text": clip(f"{BOT_NAME} · detected {card['detected']}", LIMITS["footer"])},
        "timestamp": card.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }
    ping = bool(card.get("ping")) and card.get("kind") != "test"
    return {
        "username": BOT_NAME,
        "content": "@everyone" if ping else ("*(test: no ping)*" if card.get("kind") == "test" else ""),
        "allowed_mentions": {"parse": ["everyone"] if ping else []},
        "embeds": [embed],
    }


def notice_embed(tone, title, text, ping=False):
    """A plain notice (blind spot, recovery) in the same style."""
    return {
        "username": BOT_NAME,
        "content": "@everyone" if ping else "",
        "allowed_mentions": {"parse": ["everyone"] if ping else []},
        "embeds": [{"title": clip(title, LIMITS["title"]), "description": clip(text, LIMITS["description"]), "color": COLORS.get(tone, COLORS["unknown"]),
                    "footer": {"text": BOT_NAME}, "timestamp": datetime.now(timezone.utc).isoformat()}],
    }


def embed_size(payload):
    """Characters Discord counts toward the 6000 total (title, description, field names/values, footer)."""
    total = 0
    for embed in payload.get("embeds", []):
        total += len(embed.get("title", "")) + len(embed.get("description", "")) + len(embed.get("footer", {}).get("text", ""))
        total += sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
    return total


def post(payload, wait=False):
    """Send a payload; returns (status code, parsed JSON or None). Never raises."""
    if not DISCORD_WEBHOOK_URL:
        print("Discord: no DISCORD_WEBHOOK_URL secret, skip")
        return None, None
    try:
        response = requests.post(DISCORD_WEBHOOK_URL + ("?wait=true" if wait else ""), json=payload, timeout=10)
        print(f"Discord: HTTP {response.status_code}")
        return response.status_code, (response.json() if wait and response.status_code == 200 else None)
    except Exception as exc:
        print(f"Discord send failed: {exc}")
        return None, None


def send_card(card):
    return post(stock_embed(card))


def alert(title, body, url="", ping=False, tone="blind"):
    """Generic notice (kept for callers that only have text)."""
    return post(notice_embed(tone, title, body + (f"\n{url}" if url else ""), ping))
