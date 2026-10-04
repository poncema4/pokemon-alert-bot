"""Continuous monitor: checks every ~60 seconds instead of waiting on GitHub's cron.

GitHub's `*/5` cron is best effort; measured over 100 scheduled runs the median gap was 239 minutes
(longest 499), so a restock that lasts a few minutes was usually missed. This runner keeps one job alive
for ~55 minutes and polls inside it; the workflow starts the next job when this one ends.

Every cycle: fast pass (seed + known listings), refresh the live-hit map, announce new listings.
Every DISCOVER_EVERY-th cycle also runs the slower keyword searches. State is committed every COMMIT_EVERY seconds (a heartbeat: even when nothing changed, so the site can show how recently the
stores were checked), and at once
when the live-hit list changes.

Also runs on an always-on machine: `python monitor_loop.py` (set MONITOR_RUNTIME_SECONDS=0 to run forever).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root (this file lives in bot/)
CYCLE_SECONDS = float(os.environ.get("MONITOR_CYCLE_SECONDS", "30"))
RUNTIME_SECONDS = float(os.environ.get("MONITOR_RUNTIME_SECONDS", "3300"))
DISCOVER_EVERY = int(os.environ.get("MONITOR_DISCOVER_EVERY", "20"))  # about every 10 minutes at a 30 s cycle
COMMIT_EVERY = float(os.environ.get("MONITOR_COMMIT_EVERY", "300"))
PRICE_EVERY = int(os.environ.get("MONITOR_PRICE_EVERY", "40"))  # cycles between 30th price refreshes (about 20 minutes)
MARKET_EVERY = int(os.environ.get("MONITOR_MARKET_EVERY", "20"))  # cycles between TCGplayer market refreshes for tracked listings
TRACKED = ("data/state.json", "data/gamestop_pids.json", "docs/alerts.json", "docs/health.json", "docs/30th_prices.json", "docs/market.json", "docs/coverage.json")


def signature():
    """What counts as a meaningful change: state without last_seen, the live-hit list, health without last_run."""
    def load(name):
        try:
            return json.loads((ROOT / name).read_text(encoding="utf-8"))
        except Exception:
            return {}
    state = {k: ({f: v for f, v in e.items() if f != "last_seen"} if isinstance(e, dict) else e) for k, e in load("data/state.json").items()}
    health = {r: {f: v for f, v in e.items() if f != "last_run"} for r, e in load("docs/health.json").items()}
    prices = {k: v for k, v in load("docs/30th_prices.json").items() if k not in ("checked_at",)}
    market = {k: {f: v for f, v in e.items() if f != "updated_at"} for k, e in load("docs/market.json").items()}
    coverage = {k: v for k, v in load("docs/coverage.json").items() if k != "updated_at"}
    return json.dumps([state, load("docs/alerts.json"), health, prices, market, coverage], sort_keys=True), json.dumps(load("docs/alerts.json"), sort_keys=True)


def start_browser():
    """Open the shared browser if Playwright is installed and enabled (POKEPING_BROWSER=1); otherwise the browser-read stores are skipped."""
    import monitor
    if os.environ.get("POKEPING_BROWSER") != "1" or monitor.BROWSER is not None:
        return
    try:
        from browser_reader import BrowserReader
        monitor.BROWSER = BrowserReader()
        print("browser reader started")
    except Exception as exc:
        print(f"browser reader unavailable: {exc}")


def run_cycle(number):
    import monitor
    import notify_new_listings
    import refresh_live_hits
    if number % PRICE_EVERY == 0:
        try:
            import update_30th_prices
            update_30th_prices.main()
        except Exception as exc:  # prices must never stop stock checks
            print(f"30th price refresh failed: {exc}")
    if number % MARKET_EVERY == 1 % MARKET_EVERY:
        try:
            refresh_market()
        except Exception as exc:  # the advisor is a nicety: it must never stop stock checks
            print(f"market refresh failed: {exc}")
    monitor.main(discover=number % DISCOVER_EVERY == 0, cycle=number)
    try:
        import coverage
        coverage.main()
    except Exception as exc:  # the board is informational and must never stop stock checks
        print(f"coverage board failed: {exc}")
    refresh_live_hits.main()
    notify_new_listings.main()


def refresh_market():
    """Refresh the cached TCGplayer market price of every listing the bot tracks (seed URLs plus readable discoveries)."""
    import advisor
    import monitor
    config = monitor.load_json(monitor.CONFIG_FILE, {})
    state = monitor.load_json(monitor.STATE_FILE, {})
    cache = monitor.load_json(monitor.MARKET_FILE, {})
    items = [(entry.get("title", ""), key.split("::", 1)[1]) for key, entry in state.items() if isinstance(entry, dict) and "::" in key and entry.get("last_ok")]
    refreshed = advisor.refresh_cache(config, cache, items) + advisor.refresh_watchlist(config, cache)
    monitor.save_json(monitor.MARKET_FILE, cache)
    print(f"market prices refreshed: {refreshed} (tracked listings and watched ETBs)")


def commit_and_push():
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    git("config", "user.name", "Pokémon Alert Bot")
    git("config", "user.email", "pokemon-alert-bot@users.noreply.github.com")
    git("add", *TRACKED)
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    git("commit", "-m", "Update stock state [skip ci]")
    for attempt in range(3):
        git("pull", "--rebase", "--autostash")
        if git("push").returncode == 0:
            return True
        time.sleep(2 + attempt * 3)
    print("push failed after 3 attempts; will retry on the next commit")
    return False


def run_loop(cycle=run_cycle, commit=commit_and_push, sig=signature, now=time.monotonic, sleep=time.sleep,
             runtime=RUNTIME_SECONDS, interval=CYCLE_SECONDS, commit_every=COMMIT_EVERY):
    """Scheduling only (clock, cycle and commit are injected so it is testable). Returns cycles run."""
    start = last_commit = now()
    last_sig = sig()
    if cycle is run_cycle:
        start_browser()
    number = 0
    while True:
        began = now()
        try:
            cycle(number)
        except Exception as exc:  # one bad cycle must never stop the watcher
            print(f"cycle {number} failed: {exc}")
        number += 1
        full, alerts = sig()
        alerts_changed = alerts != last_sig[1]
        # A changed live-hit list is committed at once. Otherwise a HEARTBEAT every commit_every seconds: when every store is out of stock nothing
        # "meaningful" changes, and without it last_seen / last_run were never committed, so the site said "checked 38 minutes ago" for a
        # watcher that was running fine.
        if alerts_changed or now() - last_commit >= commit_every:
            if commit():
                last_sig = (full, alerts)
            last_commit = now()
        if runtime and now() - start + interval > runtime:
            break
        sleep(max(1.0, interval - (now() - began)))
    commit()
    return number


if __name__ == "__main__":
    print(f"monitor loop: every {CYCLE_SECONDS:.0f}s for {RUNTIME_SECONDS:.0f}s (discovery every {DISCOVER_EVERY} cycles)")
    run_loop()
