# Pokémon stock & promo alert bot

Personal monitor for North Arlington / North Jersey. Discord alerts plus a live map of nearby stores and ping points.

This is an **alert** bot. It does not buy cards or bypass retailer bot walls.

## What still works on GitHub Actions

| Source | On GitHub Actions |
|---|---|
| Target + Walmart product search | Best-effort. Cloud IPs sometimes get a bot page. |
| Slickdeals frontpage RSS | Usually works. Catches Best Buy-style **promos** without scraping Best Buy. |
| Pokémon.com news RSS | Usually works. |
| r/PokemonTCGDeals | Best-effort. |
| Best Buy + GameStop websites | **Not scraped** (they block GitHub). They stay on the **map** for in-person snipes. |

GitHub runs this for you about **every 5 minutes**. GitHub often fires a few minutes late. That is a GitHub limit, not something you fix in the workflow.

---

## What you still have to do (I cannot click these)

I can push code. I cannot add your Discord webhook or turn on Pages in your GitHub account.

### 1. Discord webhook (required for pings)

1. Discord → your server → `#pokemon-alerts` (create the channel if needed)
2. Channel settings → **Integrations → Webhooks → New Webhook** → Copy URL
3. GitHub → this repo → **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: paste the webhook URL
4. Phone: Discord app → that channel → notifications **All messages**

Optional secret: `DISCORD_PING` = `true` if you want `@everyone`.

### 2. GitHub Actions

After this code is on `main`:

1. Repo → **Actions**
2. If you see **I understand my workflows, go ahead and enable them** — click it (one-time)
3. **Pokemon Stock Monitor** → **Run workflow** → **Run workflow** (this is the test)
4. Open the run → **check-stock** → **Run stock check**. You should see search/RSS log lines.

After that, leave it alone. The schedule keeps it going.

### 3. Map

1. **Settings → Pages** → Deploy from a branch → `main` / folder `/docs` → Save
2. Map URL: https://poncema4.github.io/pokemon-alert-bot/

---

## How to test

1. Run workflow once (step 2 above).
2. Confirm the job is green and the log is not empty.
3. You will **not** get a Discord ping on the first successful run on purpose — it only **remembers** current listings so you are not spammed. Later restocks, new promos, and new deal posts ping Discord.
4. Optional local test (no Discord unless you set the env var):

```bash
pip install -r requirements.txt
python monitor.py
```

---

## How often it runs with you doing nothing

- **Automatic:** every **5 minutes** (`*/5 * * * *`), 24/7, on GitHub’s free runners
- **You:** Discord secret once, enable Actions once, enable Pages once
- **Faster (optional):** `python run_loop.py` on a PC that stays on (every 20 seconds). Not required.
