# Pokémon TCG Stock & Listing Alert Bot

A personal Pokémon TCG monitor for North Jersey. It watches the **Big 4 retailers** for online Pokémon product listings and availability, sends clean Discord alerts, and powers a live map with nearby stores and niche-shop context.

> **Important:** This is an alerting system. It does not buy products, bypass retailer protections, or guarantee that a product will still be purchasable after an alert. Retailer pages can change between the bot's check and your click.

## Core rules

### Discord + live-hit rules

Only these four retailers can generate Discord alerts or live-hit pins:

1. **Target**
2. **Walmart**
3. **Best Buy**
4. **GameStop**

Niche stores are **map-only**. They can appear on the map and can provide product links when an online shop is found, but they do **not** generate Discord notifications.

Slickdeals, Reddit and other discovery sources are supporting sources only; they are not treated as a fifth retailer.

### Three alert types

#### 1. `IN STOCK — Retailer`

Used for an **already-known** product when the page provides a positive availability signal such as `InStock`, `Add to Cart`, shipping availability, or another supported cart signal.

Example:

```text
@everyone
IN STOCK — Target

Pokémon Destined Rivals Elite Trainer Box
The retailer page is showing a verified availability/cart signal.
Refresh the product page yourself because stock can change or be hidden by retailer anti-bot systems.

Time Posted: August 27, 2026 12:42 PM EDT
Detected: August 27, 2026 12:44 PM EDT
Map: https://poncema4.github.io/pokemon-alert-bot/
Product: https://www.target.com/...
```

#### 2. `NEW LISTING — Retailer`

Used the **first time the bot discovers a Big 4 product URL**. It fires even if the current stock state is `IN STOCK`, `OUT OF STOCK`, or `AVAILABILITY UNKNOWN`.

This is intentionally the most important discovery alert because a product can appear, disappear, and reappear quickly.

The message includes the current status, the retailer-published time when one is actually exposed by the page, the bot detection time, the map, and the direct product URL.

#### 3. `AVAILABILITY UNKNOWN — Retailer`

Used when an **existing** product was previously measurable but the current page no longer gives a reliable stock signal. This can happen because a retailer blocks the runner, changes its page, or hides inventory behind JavaScript/session state.

This is **not** treated as proof of being out of stock. It means exactly what it says: refresh the product page yourself.

## Alert formatting

Each alert contains only the useful navigation links:

- **Map:** the Pokémon alert map
- **Product:** the direct retailer product page

The Discord alert does **not** dump a list of Target/Kearny/Clifton/Paramus map URLs. Local store details belong on the map.

Multiple new listings in the same run are separated by a clear divider so they do not look like one giant alert.

## Time Posted vs. Detected

The bot records two different timestamps:

- **Time Posted:** a timestamp extracted from retailer page metadata when the retailer actually exposes one.
- **Detected:** when this bot observed the listing during its monitor run.

These are deliberately not treated as the same thing. Many retail product pages do **not** expose a reliable public publication timestamp. In that case the alert says `Time Posted: Not published by the retailer` rather than inventing a timestamp.

The difference between the two is the useful detection-latency measurement when both timestamps exist.

## Accuracy testing

The bot now uses a **data-first accuracy workflow**. We are not training an ML model on the bot's own guesses.

### What you verify

When you receive a Big 4 alert, check the direct product page yourself and record the actual result:

- Was it actually purchasable?
- Was it out of stock?
- Did a refresh make it appear?
- Was the bot blocked or unable to determine stock?
- When did you personally verify it?

Those observations are stored in `data/ground_truth.json`.

### You can send verification through ChatGPT

You do **not** need to edit JSON manually. Send a message such as:

```text
Verify this alert:
Walmart — Destined Rivals ETB
Product: https://www.walmart.com/...
Alert time: 1:03 PM EDT
I clicked at 1:04 PM and it was actually in stock and I could add it to cart.
```

Then the observation can be normalized into the ground-truth dataset.

The important rule is that the label must come from what **you actually saw**, not from the bot's prediction.

### Metrics

`accuracy.py` supports measuring:

- Precision
- Recall
- F1
- Accuracy
- True positives
- False positives
- False negatives
- True negatives
- Detection latency when posted and detected timestamps are available

Once enough independent observations have been collected, we can evaluate whether an ML classifier would improve ranking/confidence. Until then, deterministic retailer rules remain in control of notifications.

## Clean-start state

The runtime state was intentionally reset for the current accuracy baseline:

- `state.json` contains only the schema version.
- `docs/alerts.json` is empty.
- `data/ground_truth.json` starts with zero observations.

That means old Discord alerts and old bot state are not being used as evidence for the new accuracy measurements.

## Repository organization

```text
pokemon-alert-bot/
├── .github/
│   └── workflows/
│       └── monitor.yml          # Scheduled GitHub Actions monitor
├── data/
│   ├── ground_truth.json        # Human-verified accuracy observations
│   └── README.md                # How to label observations
├── docs/
│   ├── index.html               # Live map
│   ├── stores.json              # Store/location data
│   └── alerts.json              # Short-lived map alert feed
├── tests/
│   └── test_accuracy.py         # Deterministic detection/URL tests
├── accuracy.py                  # Accuracy metrics and latency helpers
├── monitor.py                   # Retailer discovery + stock classification
├── notify.py                    # Discord webhook helper
├── notify_new_listings.py       # First-discovery Big 4 alerts
├── run_loop.py                  # Optional local fast loop
├── search_config.json            # Retailers, keywords and monitor settings
├── state.json                   # Runtime state (intentionally at root for Actions compatibility)
├── requirements.txt
└── README.md
```

The runtime files `state.json` and `search_config.json` intentionally remain at the repository root so the existing GitHub Actions workflow and map pipeline continue to work without fragile path changes.

## GitHub Actions cadence

The monitor requests a run every five minutes, offset away from the exact top of the hour. GitHub documents five minutes as the shortest supported scheduled-workflow interval and warns that scheduled runs can be delayed during periods of high Actions load. citeturn0search0turn0search1

The workflow also runs the deterministic accuracy tests before the stock monitor. If the tests fail, the monitor job does not proceed.

## Setup

### Discord

Create a Discord webhook and save it as the repository secret:

```text
DISCORD_WEBHOOK_URL
```

Optional:

```text
DISCORD_PING=true
```

### GitHub Actions

The workflow is under `.github/workflows/monitor.yml` and supports both its scheduled run and a manual `workflow_dispatch` run.

### Map

GitHub Pages should deploy the `docs/` directory from `main`.

Map:

```text
https://poncema4.github.io/pokemon-alert-bot/
```

## What a successful clean-start cycle looks like

1. Monitor discovers a Big 4 product URL.
2. State records it.
3. `NEW LISTING` is sent with the current stock status.
4. Later checks continue measuring the same URL.
5. If it becomes verified in stock, `IN STOCK` is sent.
6. If stock becomes unverifiable after previously being known, `AVAILABILITY UNKNOWN` is sent.
7. Niche shops remain visible on the map but never enter Discord.
8. You verify the alert manually and provide the result for the ground-truth dataset.
9. Accuracy metrics are calculated from those independent labels.
