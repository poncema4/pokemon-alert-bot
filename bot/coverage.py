"""Coverage board: for every watched Elite Trainer Box, what does each retailer say right now?

Output (docs/coverage.json) feeds the site's ETBs page. Each cell is one of:
  in_stock      a listing the bot can read says it is in stock
  out           the bot can read it and it is not in stock
  unreadable    a listing is tracked but the bot cannot read it (blocked, placeholder, no signal)
  not_tracked   the bot has no listing of this product at this retailer yet
The point is honesty: a retailer the bot cannot see is shown as such, never as "out of stock".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

RETAILERS = ("target", "walmart", "bestbuy", "gamestop", "pokemoncenter")
ALWAYS_UNREADABLE = {"pokemoncenter": "Pokémon Center blocks automated visitors"}


def has_word(text, word):
    """Whole-word match, so "151" never matches inside a product id like A-1011514."""
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def matches(item, title, url):
    """True when a listing is this watchlist product's regular Elite Trainer Box (not a Pokémon Center box, not a case)."""
    text = f"{title} {re.sub(r'https?://[^/]+', '', url)}".lower().replace("-", " ").replace("/", " ")
    if not has_word(text, "elite trainer box") and not has_word(text, "etb"):
        return False
    if not all(has_word(text, w) for w in item["include"]):
        return False
    if any(has_word(text, w) for w in item.get("exclude", [])):
        return False
    return not any(has_word(text, w) for w in ("case", "pokemon center", "pokémon center", "exclusive"))


def cell(listings):
    """Summarise the listings of one product at one retailer."""
    if not listings:
        return {"state": "not_tracked"}
    live = [l for l in listings if l["in_stock"] is True]
    if live:
        best = min(live, key=lambda l: l["price"] if l["price"] else 10 ** 9)
        return {"state": "in_stock", "price": best["price"], "url": best["url"], "since": best.get("since")}
    if any(l["in_stock"] is False for l in listings):
        known = next(l for l in listings if l["in_stock"] is False)
        return {"state": "out", "price": known["price"], "url": known["url"]}
    return {"state": "unreadable", "url": listings[0]["url"], "reason": listings[0].get("reason")}


def build_coverage(state, watchlist, market, now=None):
    now = now or datetime.now(timezone.utc)
    rows = []
    for item in watchlist:
        per = {r: [] for r in RETAILERS}
        for key, entry in state.items():
            if not isinstance(entry, dict) or "::" not in key:
                continue
            retailer, url = key.split("::", 1)
            if retailer in per and matches(item, entry.get("title", ""), url):
                per[retailer].append({"in_stock": entry.get("in_stock"), "price": entry.get("price"), "url": url, "since": entry.get("in_stock_since"), "reason": entry.get("reason")})
        cells = {r: cell(per[r]) for r in RETAILERS}
        for retailer, why in ALWAYS_UNREADABLE.items():
            if cells[retailer]["state"] == "not_tracked":
                cells[retailer] = {"state": "unreadable", "reason": why}
        m = market.get(f"watch:{item['id']}") or {}
        rows.append({"id": item["id"], "label": item["label"], "msrp": item.get("msrp"), "market": m.get("market"), "market_updated_at": m.get("updated_at"),
                     "market_url": f"https://www.tcgplayer.com/product/{m['product_id']}" if m.get("product_id") else None, "retailers": cells})
    return {"updated_at": now.isoformat(), "rows": rows}


def main():
    """Write docs/coverage.json from the current state, config and cached market prices."""
    import json
    import advisor
    from monitor import CONFIG_FILE, MARKET_FILE, STATE_FILE, load_json, save_json
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})
    market = load_json(MARKET_FILE, {})
    out = build_coverage(state, config.get("watchlist", []), market)
    save_json(STATE_FILE.parent.parent / "docs" / "coverage.json", out)
    states = [c["state"] for r in out["rows"] for c in r["retailers"].values()]
    print(f"coverage: {len(out['rows'])} ETBs; in stock {states.count('in_stock')}, out {states.count('out')}, unreadable {states.count('unreadable')}, not tracked {states.count('not_tracked')}")
