# PokePing

A personal Pokémon TCG restock alert bot for North Jersey. It watches Target, Walmart, Best Buy, GameStop and Pokémon Center, pings Discord when something is really in stock, says what the price means against live TCGplayer data, and shows the stores near home on a map.

Site: <https://poncema4.github.io/pokemon-alert-bot/> (Map, Route, 30th guide). It never buys anything and never works around a retailer's bot protection.

## How an alert is decided

- **In stock is what matters.** Price never blocks an alert; it only changes what the alert says.
- **Unknown is never in stock.** A bot wall (even one that answers HTTP 200), a 403/429, a timeout, a disabled placeholder button or a missing signal is stored as unknown and never alerts.
- **The page's own signal beats generic data.** GameStop's `data-available` flag overrides its JSON-LD (which says `InStock` for items that are not). A disabled "Add to cart" button (Target's loading placeholder) is not stock.
- **Proof is stated.** *Verified* means the page's own flag or structured data says in stock; *Likely* means only cart or pickup wording was found.
- **New listings alert once**, only if the first reading is in stock, at most 5 per run (a summary covers the rest).
- **One listing, one URL.** Fragments, query strings and trailing slashes are dropped, so a product linked several ways is not counted several times.
- **Repeat alerts are cooled down** per listing, and a blocked check can never suppress a later real restock.

## The Discord alert

One embed per alert, coloured by the price verdict, with `@everyone` in the message when `DISCORD_PING` is on:

| Part | Example |
| --- | --- |
| Title | `🟢 IN STOCK · GameStop` (`🆕 NEW LISTING IN STOCK`, `🧪 TEST ALERT`) |
| Product | the name, as a bold hyperlink (no giant image preview) |
| Verdict | `BUY: LOW`, `FAIR PRICE`, `ABOVE MARKET`, or `AT RETAIL` / `ABOVE MSRP` when no market price exists |
| Fields | price, retail (and `above MSRP (+70%)`), TCGplayer market with its age, proof, links to product / map / TCGplayer |

The verdict compares the listed price with the live TCGplayer market price: 10% or more under is `BUY: LOW`, within 10% is `FAIR PRICE`, more than 10% over is `ABOVE MARKET`. The market price is looked up by product name (`bot/advisor.py`), cached in `docs/market.json` and never older than 10 minutes when an alert is sent. If TCGplayer cannot be reached the alert simply omits the comparison.

Blind-spot notices (below) use the same card style.

## What the bot can and cannot see

Measured from GitHub's runner. The map's status lamps show the live state and explain each one on hover.

| Retailer | Reading? | Why |
| --- | --- | --- |
| GameStop | yes | its page carries its own availability flag |
| Walmart | sometimes | redirects automated visitors to a bot wall; read whenever it lets the bot in |
| Target | no | server page shows a disabled placeholder; real stock comes from a captcha-protected API |
| Best Buy | no | the connection from the runner times out |
| Pokémon Center | no | 403 or a robot check |

A retailer that cannot be read looks exactly like "nothing in stock", so each run records per-retailer counts in `docs/health.json`. After 24 hours unreadable the bot posts one **BLIND SPOT** notice, and one **RECOVERED** notice when it can read again. Listings never readable for 7 days are pruned (seed URLs stay).

## The site (`docs/`, served by GitHub Pages)

- **Map**: live online hits (one compact row each, with price and nearest store), stores sorted by distance from home, click a card to zoom to its pin, closed stores in red, status lamps for each retailer.
- **Route**: the fixed school-day sweep with real road miles and drive minutes (OSRM), live open/closed status and weekly hours.
- **30th guide**: every 30th Celebration product with live TCGplayer market price, premium over MSRP, target buy price, verdict, and the real top chase cards.

Open/closed comes from each store's weekly schedule in `docs/stores.json` (`hours`, with `hours_source` and `hours_checked`), read in Eastern time by one shared reader in `docs/js/common.js`; the pages re-read the clock every 15 seconds so a store flips at its opening and closing minute. A store whose schedule cannot be read shows "Hours?", never "Open".

Every script and stylesheet link in `docs/*.html` carries a content hash (`js/common.js?v=1a2b3c4d`) so a browser can never pair an old script with a new page. After changing anything in `docs/js` or `docs/css`, run `python tools/stamp_assets.py` (the tests fail if you forget).

Home is hard-set in `docs/stores.json`. Store pins come from each store's own OpenStreetMap record where one exists (otherwise the exact street-address point), and `pin_source` says which; `tools/check_pins.py` re-checks them.

## How it runs

`.github/workflows/monitor.yml` runs the tests, then one long-lived watcher job (`bot/monitor_loop.py`, about 55 minutes) that starts its own successor. GitHub's cron is best effort (measured median gap between scheduled runs was 239 minutes), so the job polls inside itself every 60 seconds and the 30-minute cron is only a safety net.

Each cycle checks the seed and recently readable listings, refreshes the live-hit list and announces new listings. Every 10th cycle also searches by keyword for new listings and refreshes market prices, and about every 20 cycles the 30th guide prices refresh from TCGplayer. State is committed when something meaningful changed (not just `last_seen`) and at once when the live-hit list changes. On an always-on machine, `python bot/monitor_loop.py` does the same.

| Workflow | Purpose |
| --- | --- |
| `monitor.yml` | tests on every PR and push; the watcher on schedule or manual dispatch |
| `30th-prices.yml` | hourly fallback refresh of the 30th guide prices |
| `snapshot.yml` | manual: saves the retailer pages exactly as the runner receives them |
| `verify-deployed.yml` | after every Pages build: loads the live site in a real browser and checks it works and matches the repo (`tools/verify_deployed.py`) |
| `reader-probe.yml` | manual: shows what the browser reader sees on Best Buy from a runner |
| `test-alert.yml` | manual: posts clearly labelled TEST alerts and prints Discord's answer |

Secrets: `DISCORD_WEBHOOK_URL` (required for alerts), `DISCORD_PING=true` (optional, adds `@everyone`).

## Repository layout

```text
bot/        everything that runs: monitor, loop, advisor, notify, new-listing and live-hit steps, 30th price updater, accuracy maths
config/     search_config.json: keywords, seed URLs, retail price (MSRP) rules, market-search rules
data/       state.json (what the bot has seen), ground_truth.json (human-checked outcomes for measuring accuracy)
docs/       the site and the public JSON it reads: stores, alerts, health, market, 30th prices
tools/      pin checker, page snapshotter, test-alert sender
tests/      Python and Node tests, plus captured real retailer and TCGplayer responses in tests/fixtures
.github/    workflows
```

## Testing

```bash
pip install -r requirements.txt
python tests/test_accuracy.py     # detection, health, advisor, embeds, loop, pins, site structure
node tests/web/common.test.js     # the page helpers
```

Tests run against captured real responses (retailer pages, TCGplayer searches), and the habit is to prove a check can fail: change one thing, watch the right test fail, restore it. Never trust a check that cannot fail.

## Measuring accuracy

`bot/accuracy.py` computes precision, recall, F1 and detection latency from `data/ground_truth.json`. Record what you actually saw when you clicked an alert (retailer, URL, alert type, whether it was purchasable, when). Never label an event from the bot's own prediction; the point is independent ground truth.
