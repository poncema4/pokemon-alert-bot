"""Send clearly-labelled TEST alerts to the Discord webhook and print what Discord answers.

Run from the `test-alert.yml` workflow (it holds the webhook secret). The samples use real current numbers (the 30th
Celebration ETB's live TCGplayer market price, and a live lookup for the Pitch Black ETB), so what you see in the channel is exactly
what a real alert looks like. Test alerts never @everyone.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import advisor  # noqa: E402
import notify  # noqa: E402

MAP_URL = json.loads((ROOT / "search_config.json").read_text(encoding="utf-8")).get("map_url", "")


def samples():
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    config = json.loads((ROOT / "search_config.json").read_text(encoding="utf-8"))
    guide = json.loads((ROOT / "docs" / "30th_prices.json").read_text(encoding="utf-8"))
    etb = next(p for p in guide["products"] if p["id"] == "etb")
    etb_market = {"market": etb["market"], "updated_at": etb.get("market_updated_at") or stamp, "product_id": 704143}
    gs_30 = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-30th-celebration-elite-trainer-box/20036324.html"
    pitch_url = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html"
    pitch_market = advisor.market_for(config, {}, "Pitch Black Elite Trainer Box", pitch_url)
    cards = [
        advisor.build_card("target", "test", "Pokémon TCG: 30th Celebration Elite Trainer Box", "https://www.target.com/p/-/A-1010892076", MAP_URL, stamp, "page", 49.99, 49.99, etb_market),
        advisor.build_card("gamestop", "test", "Pokemon Trading Card Game: Pitch Black Elite Trainer Box", pitch_url, MAP_URL, stamp, "page", 84.99, 49.99, pitch_market),
        advisor.build_card("bestbuy", "test", "Pokémon TCG: 30th Celebration Elite Trainer Box", "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ", MAP_URL, stamp, "text", 159.99, 49.99, etb_market),
    ]
    return [notify.stock_embed(c) for c in cards] + [notify.notice_embed("blind", "🧪 TEST · ⚠️ BLIND SPOT · Walmart", "Walmart has not been readable for 26 h. Every check was blocked, so this bot cannot see Walmart stock right now. Check it by hand.")]


def main():
    if not notify.DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not set; nothing sent.")
        return 1
    failures = 0
    for payload in samples():
        status, body = notify.post(payload, wait=True)
        embed = (body or {}).get("embeds", [{}])[0] if body else {}
        print(json.dumps({"http": status, "message_id": (body or {}).get("id"), "embed_title": embed.get("title"), "embed_color": embed.get("color"), "fields": [f["name"] for f in embed.get("fields", [])], "size": notify.embed_size(payload)}))
        failures += status != 200
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
