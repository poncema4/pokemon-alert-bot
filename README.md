# Pokémon TCG Stock & Listing Alert Bot

Personal North Jersey Pokémon TCG monitor. It watches Target, Walmart, Best Buy and GameStop for online Pokémon listings/stock, sends verified Discord alerts, maintains a short-lived live-hit map, and provides a school-day store route.

> The bot does not buy products, bypass retailer protections, or guarantee stock after an alert. Retailer inventory can change between detection and your click.

## Production rules

- **Big 4 only:** Target, Walmart, Best Buy, GameStop can generate Discord alerts and live-hit pins.
- **UNKNOWN is never IN STOCK:** HTTP 403/429, timeout, bot-check pages, missing signals and other unverifiable results are stored as UNKNOWN and never generate a Discord stock alert.
- **Unknown never wakes Discord:** the safety rule is enforced in code, not just by configuration.
- **NEW LISTING is verified-only:** a newly discovered URL is tracked immediately, but Discord is notified only if that first observation is verified in stock.
- **Niche stores are map-only:** they remain useful for route planning and local context without becoming alert sources.
- **No fake timestamps:** `posted_at` is populated only when retailer metadata exposes a usable timestamp; otherwise it stays null.
- **Stock cooldown is separate from UNKNOWN:** blocked checks cannot suppress a later real restock.

## Discord alerts

### IN STOCK
Sent only for an already-known listing that transitions to a verified positive availability signal. The alert contains the product, detection time, map link and direct product link.

### NEW + IN STOCK
Sent only when a newly discovered Big 4 URL is verified in stock on its first observation. Unknown/out-of-stock discoveries are tracked silently.

There is no production UNKNOWN Discord alert. That behavior is intentional and enforced in code.

## Blind spots (health)

A retailer that blocks the runner looks exactly like "nothing in stock", so the bot tracks it. Every run records per-retailer counts in `docs/health.json` (checked / readable / blocked / errors). A bot wall is recognised even when it answers HTTP 200 (Walmart redirects to `/blocked`). When a retailer has been unreadable for 24 hours the bot posts one **BLIND SPOT** message to Discord, and one **RECOVERED** message when it can read again. After three unreadable checks in a row the rest of that retailer is skipped for the run, and discovered listings that were never readable for 7 days are pruned (seed URLs stay).

In-stock alerts say how strong the evidence is: *Verified* means structured availability data, *Likely* means only cart/pickup wording was found, so confirm on the page.

## Accuracy

`tests/test_accuracy.py` covers retailer URL validation, Pokémon-product detection, structured stock parsing, protection against UNKNOWN interfering with stock cooldowns, and route-node integrity.

`accuracy.py` calculates precision, recall, F1, accuracy and detection latency from independent human observations. Human labels belong in `data/ground_truth.json`; the bot's own prediction is not ground truth.

## Website

GitHub Pages serves the `docs/` directory. The repository already has the GitHub Pages deployment integration enabled; website changes under `docs/` automatically trigger the Pages build/deployment. There is intentionally **no duplicate Pages workflow** in this repository.

The site has three main views:

- `docs/index.html` — live North Jersey map and verified online hits
- `docs/route.html` — school-day store sweep
- `docs/30th.html` — 30th Celebration investment/rip guide

The website reads `stores.json`, `alerts.json` and `30th_prices.json` directly, with cache-busting query parameters so fresh commits are picked up quickly.

## Route design

The default school-day route is deliberately **not DFS**. Each store is a graph node and every node pair has a coordinate-based edge weight. The route is a constrained weighted-graph sweep designed around the real trip rather than a generic nearest-neighbor sort.

The current locked order is:

1. Walmart Secaucus
2. Best Buy Secaucus
3. Best Buy American Dream
4. CardVault by Tom Brady — American Dream
5. Target Clifton
6. GameStop Lyndhurst
7. TCGDUCKHUNTER — Lyndhurst
8. East Coast Connection — Lyndhurst
9. Target Kearny
10. GameStop Kearny
11. **Walmart Kearny — END**

This keeps the useful northern stops together, moves progressively south, and finishes at Walmart Kearny. Paramus, North Bergen, Jersey City, West New York, Hoboken and other side-trip locations remain in `stores.json` but are not automatically inserted into the default school-day sweep just because they are nearby. A future change should promote a side trip only if it actually improves the real trip.

The route page can use the device's current coordinates as its origin. Google Maps remains responsible for actual road routing, traffic, one-way streets and closures.

## 30th Celebration price guide

`docs/30th_prices.json` contains the ranked sealed products, MSRP, current TCGplayer snapshot, target buy range, absolute max, pack count, promo/exclusive notes and chase-card ceiling. Presale/low-volume values are explicitly labeled rather than treated as established market prices.

`update_30th_prices.py` refreshes that existing JSON file conservatively. If TCGplayer cannot be parsed, the last good values are preserved. `.github/workflows/30th-prices.yml` runs the refresh hourly.

## GitHub Actions

### Stock monitor
`.github/workflows/monitor.yml` runs tests first, then one long-lived watcher job (`monitor_loop.py`, about 55 minutes). GitHub's cron is best effort (measured median gap between scheduled runs: 239 minutes), so the job polls inside itself every 60 seconds and starts its own successor when it ends; a 30-minute cron is only the safety net. Each cycle checks the seed and known listings, refreshes the live-hit map and announces new listings; every 10th cycle also runs the slower keyword searches. State is committed when something meaningful changed (never just because `last_seen` moved), and at once when the live-hit list changes. On an always-on machine, `python monitor_loop.py` does the same.

### 30th prices
`.github/workflows/30th-prices.yml` runs hourly at minute 17 plus manual dispatch and updates the existing price JSON rather than creating timestamped copies.

### Pages
GitHub's existing Pages integration publishes `docs/` after site changes. Do not add another Pages workflow unless the Pages configuration itself changes.

## Repository layout

```text
pokemon-alert-bot/
├── .github/workflows/
│   ├── monitor.yml
│   └── 30th-prices.yml
├── data/
│   ├── README.md
│   └── ground_truth.json
├── docs/
│   ├── index.html
│   ├── route.html
│   ├── 30th.html
│   ├── stores.json
│   ├── alerts.json
│   ├── 30th_prices.json
│   ├── health.json
│   └── favicon.svg
├── tests/test_accuracy.py
├── accuracy.py
├── bootstrap_stock_alerts.py
├── monitor.py
├── notify.py
├── notify_new_listings.py
├── refresh_live_hits.py
├── update_30th_prices.py
├── run_loop.py
├── search_config.json
├── state.json
├── requirements.txt
└── README.md
```

Files are updated in place. New files should be created only when they represent a genuinely new component that cannot cleanly live in an existing file; do not create duplicate versions, timestamped copies or parallel implementations.

## Discord setup

Set the repository secret:

```text
DISCORD_WEBHOOK_URL
```

Optional:

```text
DISCORD_PING=true
```

The production monitor will never send an UNKNOWN alert even if a retailer blocks the runner.
