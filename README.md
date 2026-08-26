# 🎴 Pokémon TCG Stock & Deal Alert Bot

> Never miss a restock again. Free, automated, and always watching — so you don't have to.

A lightweight bot that watches Target, Walmart, and Best Buy for Pokémon TCG products, pings your Discord the moment something goes back in stock, and flags real deals when prices drop below normal. Runs entirely on free infrastructure — no server bills, no laptop left open overnight.

---

## ✨ Features

| | |
|---|---|
| 🔍 **Auto-discovery** | Just give it search keywords — no manual product URLs to maintain |
| 📦 **Stock alerts** | Get pinged the instant a tracked item shows as available |
| 💰 **Deal detection** | Flags prices meaningfully below normal MSRP (catches promos like anniversary sales) |
| 💬 **Discord notifications** | Alerts land straight in your own server, on desktop and mobile |
| 🗺️ **Nearby store links** | Every alert includes a map link to stores near you |
| 🆓 **100% free to run** | Powered by GitHub Actions' free scheduled workflows |
| 🤖 **No sketchy tactics** | No CAPTCHA bypassing, no proxy evasion — plays it straight with retailer sites |

---

## ⚙️ How It Works

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  search_config   │ ──▶ │   monitor.py      │ ──▶ │   Discord Webhook  │
│  (your keywords) │     │  runs every 5 min │     │  (your phone/PC)   │
└─────────────────┘     └──────────────────┘     └───────────────────┘
                                 │
                                 ▼
                          ┌─────────────┐
                          │ state.json  │
                          │ (memory of  │
                          │ what's been │
                          │  seen/sold) │
                          └─────────────┘
```

1. GitHub Actions wakes the bot up every ~5 minutes (or run it continuously yourself for faster checks)
2. It searches Target, Walmart, and Best Buy for your keywords
3. It compares what it finds against what it saw last time
4. New stock or a good price? → Discord gets pinged immediately
5. Progress is saved back to the repo so you never get duplicate alerts

---

## 🚀 Quick Start

1. **Fork or clone** this repo
2. Add your Discord webhook URL as a repository secret: `DISCORD_WEBHOOK_URL`
3. Edit `search_config.json` with the products and prices you care about
4. Go to **Actions → Pokemon Stock Monitor → Run workflow** to test it
5. Sit back — it runs itself from here

---

## 📁 Project Structure

```
pokemon-alert-bot/
├── monitor.py                  # Core logic: search, check, alert
├── run_loop.py                 # Optional: run continuously on always-on hardware
├── search_config.json          # Your keywords + expected prices (edit this!)
├── state.json                  # Bot's memory — auto-updated, don't touch
├── requirements.txt            # Python dependencies
└── .github/
    └── workflows/
        └── monitor.yml         # The free scheduler that keeps this alive
```

---

## 🔧 Configuration

Everything you'll actually want to tweak lives in `search_config.json`:

- `keywords` — what to search for across all three retailers
- `expected_prices` — your reference "normal" price per category, used to catch deals
- `deal_threshold_percent` — how big a discount triggers a deal alert (default: 15%)
- `near_location` — used to generate the "nearby stores" map link in alerts

---

## ⚠️ Good to Know

- **This bot does not auto-purchase anything.** It alerts — you still click buy.
- **Adding to cart doesn't reserve stock** at most retailers; speed still matters once you're alerted.
- Target and Walmart don't offer a public stock API, so those checks read the page directly and can occasionally get blocked — that's expected, not a bug.
- GitHub's free scheduler runs roughly every 5 minutes but isn't millisecond-precise, especially during high-traffic periods.

---

## 📜 License

Personal project — built for personal use tracking your own watchlist. Not affiliated with Target, Walmart, Best Buy, or The Pokémon Company.

---

<p align="center">Built with 🔴⚪ for one more successful pull.</p>