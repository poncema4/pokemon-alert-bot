# PokePing

A personal Pokémon TCG restock alert bot for North Jersey. It watches Target, Walmart, Best Buy, GameStop and Pokémon Center, pings Discord when something is really in stock, says what the price means against live TCGplayer data, and shows the stores near home on a map.

It exists to help a collector get product at fair prices ahead of resellers: confirmed restock alerts within about a minute, a price check so you know whether a listing is a good deal, the nearest open store, and a route for the school-day sweep.

Site: <https://poncema4.github.io/pokemon-alert-bot/> (Map, Route, 30th guide).

## How an alert is decided

- **In stock is what matters.** Price never blocks an alert; it only changes what the alert says.
- **Unknown is never in stock.** A bot wall (even one that answers HTTP 200), a 403/429, a timeout, a disabled placeholder button or a missing signal is stored as unknown and never alerts.
- **The page's own signal beats generic data.** GameStop's `data-available` flag overrides its JSON-LD (which says `InStock` for items that are not). A disabled "Add to cart" button (Target's loading placeholder) is not stock.
- **Proof is stated.** *Verified* means the page's own flag or structured data says in stock; *Likely* means only cart or pickup wording was found.
- **New listings alert once**, only if the first reading is in stock, at most 5 per run (a summary covers the rest).
- **A reseller is not a restock.** Best Buy shows an enabled "Add to cart" for third-party marketplace sellers ("Sold & shipped by Shopville Inc", "More options from Marketplace sellers $94.99 - $155.94") even when Best Buy itself has none. Measured on 2026-10-04: every Best Buy "in stock" reading so far (Chaos Rising, Perfect Order, Pitch Black) was a reseller at about twice retail. A reading counts as in stock only when the seller is the store itself or the page names no seller (`bot/sellers.py`), so a first-party restock is never missed; a reseller-only page is recorded as out of stock with reason `marketplace_only`.
- **Something in stock is checked often.** Calm products are read every few cycles (Best Buy pages every ~10 minutes), but once a page is in stock it is re-read at the hot rhythm (every cycle over HTTP, about every 2 minutes in the browser), so it stays on Live online (hidden after 5 minutes without a check) and a sell-out shows quickly.
- **One alert per stay in stock.** After an alert the item is disarmed. It can alert again only after confirmed out-of-stock readings lasting 20 minutes (`rearm_minutes`); unknown readings (blocked or half-loaded pages) never re-arm it, so a flickering page cannot ping twice for the same stay.
- **One listing, one URL.** Fragments, query strings and trailing slashes are dropped, so a product linked several ways is not counted several times.
- **Repeat alerts are cooled down** per listing, and a blocked check can never suppress a later real restock.

## The Discord alert

One embed per alert, coloured by the price verdict, with `@everyone` in the message when `DISCORD_PING` is on:

| Part | Example |
| --- | --- |
| Title | `🟢 IN STOCK · GameStop` (`🆕 NEW LISTING IN STOCK`, `🧪 TEST ALERT`) |
| Product | the name, as a bold hyperlink (no giant image preview) |
| Verdict | `BUY: LOW`, `FAIR PRICE`, `ABOVE MARKET`, or `AT RETAIL` / `ABOVE MSRP` when no market price exists |
| Fields | price, retail (and `above MSRP (+70%)`), TCGplayer market with its age, proof, links: **Product page**, **Add to cart** (Walmart and Best Buy only: tapping it puts the item in your cart, you check out yourself), **Map**, **TCGplayer price** |

The verdict compares the listed price with the live TCGplayer market price: 10% or more under is `BUY: LOW`, within 10% is `FAIR PRICE`, more than 10% over is `ABOVE MARKET`. The market price is looked up by product name (`bot/advisor.py`), cached in `docs/market.json` and never older than 10 minutes when an alert is sent. If TCGplayer cannot be reached the alert simply omits the comparison.

Blind-spot notices (below) use the same card style.

## What the bot can and cannot see

Measured from GitHub's runner. The map's status lamps show the live state and explain each one on hover.

| Retailer | Reading? | Why |
| --- | --- | --- |
| GameStop | yes | its page carries its own availability flag |
| Walmart | sometimes | redirects automated visitors to a robot check ("Robot or human?"); read whenever it lets the bot in |
| Target | no | server page shows a disabled placeholder; real stock comes from a captcha-protected API |
| Best Buy | spotty | read in a real browser; its bot defence serves an empty page about half the time (an empty page is "unknown", never "in stock"; a half-loaded page is reloaded once, a robot wall never is), and only the store's own offers count, not marketplace resellers |
| Pokémon Center | no | 403 or a robot check |

A retailer that cannot be read looks exactly like "nothing in stock", so each run records per-retailer counts in `docs/health.json`. After 24 hours unreadable the bot posts one **BLIND SPOT** notice, and one **RECOVERED** notice when it can read again. Listings never readable for 7 days are pruned (seed URLs stay).

## The site (`docs/`, served by GitHub Pages)

- **Map**: live online hits (one compact row each, with price and nearest store), stores sorted by distance from home, click a card to zoom to its pin, closed stores in red, status lamps for each retailer.
- **Route**: the fixed school-day sweep with real road miles and drive minutes (OSRM), live open/closed status and weekly hours.
- **30th guide**: every 30th Celebration product with live TCGplayer market price, premium over MSRP, target buy price, verdict, and the real top chase cards.

Open/closed comes from each store's weekly schedule in `docs/stores.json` (`hours`, with `hours_source` and `hours_checked`), read in Eastern time by one shared reader in `docs/js/common.js`; the pages re-read the clock every 15 seconds so a store flips at its opening and closing minute. A store whose schedule cannot be read shows "Hours?", never "Open".

**Add to cart links.** Walmart's is built from the item id in the product URL. Best Buy's needs the 7-digit SKU (its product codes such as `JJG2TL8XCJ` are rejected as "Invalid SKU"): the browser reader takes it from the page and the watcher remembers it in `state.json`; `bestbuy_skus` in `config/search_config.json` holds the ones verified against Best Buy's own `/sku/` pages. Target, GameStop and Pokémon Center publish no such link, so their alerts use the product page's own button.

Every script and stylesheet link in `docs/*.html` carries a content hash (`js/common.js?v=1a2b3c4d`) so a browser can never pair an old script with a new page. After changing anything in `docs/js` or `docs/css`, run `python tools/stamp_assets.py` (the tests fail if you forget).

Home is hard-set in `docs/stores.json`. Store pins come from each store's own OpenStreetMap record where one exists (otherwise the exact street-address point), and `pin_source` says which; `tools/check_pins.py` re-checks them.

## How it runs

`.github/workflows/monitor.yml` runs the tests, then one long-lived watcher job (`bot/monitor_loop.py`, about 55 minutes) that starts its own successor. GitHub's cron is best effort (measured median gap between scheduled runs was 239 minutes), so the job polls inside itself every 30 seconds and the 30-minute cron is only a safety net.

If a watcher job is killed outright (a lost runner, the job timeout) its hand-over step never runs, so `watchdog.yml` runs whenever any monitor run ends and `tools/ensure_watcher.py` starts a watcher if none is running or queued. Hand-over, watchdog and the 30-minute cron are three independent ways the watcher comes back.

The watcher runs in its own concurrency group (`pokeping-watcher`), separate from the test runs on push and PR. GitHub keeps only one *pending* run per group, so when they shared one, every merge to `main` replaced the watcher's queued handover and the chain broke until the next cron run (the data went stale after each merge).

Each cycle checks the seed and recently readable listings, refreshes the live-hit list and announces new listings. Every 10th cycle also searches by keyword for new listings and refreshes market prices, and about every 20 cycles the 30th guide prices refresh from TCGplayer. State is committed every 5 minutes as a heartbeat (even when nothing changed, so the site's "checked N min ago" stays current) and at once when the live-hit list changes. On an always-on machine, `python bot/monitor_loop.py` does the same.

| Workflow | Purpose |
| --- | --- |
| `monitor.yml` | tests on every PR and push; the watcher on schedule or manual dispatch |
| `30th-prices.yml` | hourly fallback refresh of the 30th guide prices |
| `snapshot.yml` | manual: saves the retailer pages exactly as the runner receives them |
| `watchdog.yml` | whenever a monitor run ends: starts a watcher if none is running or queued |
| `verify-deployed.yml` | after every Pages build: loads the live site in a real browser and checks it works and matches the repo (`tools/verify_deployed.py`) |
| `reader-probe.yml` | manual: shows what the browser reader sees on Best Buy from a runner |
| `test-alert.yml` | manual: posts clearly labelled TEST alerts and prints Discord's answer |

Secrets: `DISCORD_WEBHOOK_URL` (required for alerts), `DISCORD_PING=true` (optional, adds `@everyone`).

## Alerts from the stores' own emails (Target, Walmart, Pokémon Center and the rest)

Some stores cannot be read by a bot at all (Target and Walmart put up captchas, Pokémon Center blocks automated visitors), and the bot will not try to get past that. They can still tell **you**: every one of them emails you when a sold-out item comes back if you ask. `integrations/gmail_to_discord.gs` is a small Google Apps Script that runs in your own Gmail and posts those emails to the same Discord channel, with an `@everyone` ping, within about a minute. It is a second source, not PokePing's own check: the store decides when its email goes out, so it can be late.

One-time setup (about 10 minutes):

1. **Ask each store to email you.** On a sold-out product page tap Target's "Notify me when it's back", Walmart's "Get in-stock alert", GameStop's "Notify Me" (and Best Buy's in-stock alert where the page offers one). For Pokémon Center, subscribe to its emails (restock, queue and early-access mail). Do it for the boxes you want.
2. **Label them in Gmail.** Settings > Filters > Create a new filter, "From": `target.com OR walmart.com OR bestbuy.com OR gamestop.com OR pokemoncenter.com OR pokemon.com`, then **Apply the label** `PokePing` (create it) and tick "Never send it to Spam".
3. **Add the script.** Open script.google.com, New project, paste the whole of `integrations/gmail_to_discord.gs`.
4. **Give it the webhook.** Project Settings > Script properties > add `DISCORD_WEBHOOK_URL` with the same Discord webhook PokePing uses (keep it private).
5. **Test, then start it.** Run `sendTestToDiscord` once (a TEST message appears in Discord), then run `installTrigger` once and approve the permissions. It now checks every minute.

What it forwards: unread emails labelled `PokePing`, from one of the five stores' own domains (a look-alike domain is ignored), that talk about stock (back in stock, available, restock, queue, waiting room), less than a day old. Each is forwarded once and then marked read. A subject can never ping anyone by itself. `node tests/web/gmail_forwarder.test.js` tests this logic.

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
python tests/test_end_to_end.py   # a restock goes through the real watcher code to a (local) Discord webhook and onto the real site in Chromium
```

The end-to-end test stages a restock at Target, Walmart, Best Buy and GameStop and checks that Discord and Live online agree (title, price, link), that nothing pings twice for the same stay (even through a blocked reading), that a sell-out clears Live online, and that Pokémon Center is shown as "can't read" and never alerts. CI runs it on every PR.

Tests run against captured real responses (retailer pages, TCGplayer searches), and the habit is to prove a check can fail: change one thing, watch the right test fail, restore it. Never trust a check that cannot fail.

## Measuring accuracy

`bot/accuracy.py` computes precision, recall, F1 and detection latency from `data/ground_truth.json`. Record what you actually saw when you clicked an alert (retailer, URL, alert type, whether it was purchasable, when). Never label an event from the bot's own prediction; the point is independent ground truth.
