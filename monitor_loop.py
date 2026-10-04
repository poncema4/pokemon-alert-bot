"""Continuous monitor: checks every ~60 seconds instead of waiting on GitHub's cron.

GitHub's `*/5` cron is best effort; measured over 100 scheduled runs the median gap was 239 minutes
(longest 499), so a restock that lasts a few minutes was usually missed. This runner keeps one job alive
for ~55 minutes and polls inside it; the workflow starts the next job when this one ends.

Every cycle: fast pass (seed + known listings), refresh the live-hit map, announce new listings.
Every DISCOVER_EVERY-th cycle also runs the slower keyword searches. State is committed when something
meaningful changed (never just because `last_seen` moved), at most every COMMIT_EVERY seconds, and at once
when the live-hit list changes.

Also runs on an always-on machine: `python monitor_loop.py` (set MONITOR_RUNTIME_SECONDS=0 to run forever).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).parent
CYCLE_SECONDS = float(os.environ.get("MONITOR_CYCLE_SECONDS", "60"))
RUNTIME_SECONDS = float(os.environ.get("MONITOR_RUNTIME_SECONDS", "3300"))
DISCOVER_EVERY = int(os.environ.get("MONITOR_DISCOVER_EVERY", "10"))
COMMIT_EVERY = float(os.environ.get("MONITOR_COMMIT_EVERY", "300"))
TRACKED = ("state.json", "docs/alerts.json", "docs/health.json")


def signature():
    """What counts as a meaningful change: state without last_seen, the live-hit list, health without last_run."""
    def load(name):
        try:
            return json.loads((ROOT / name).read_text(encoding="utf-8"))
        except Exception:
            return {}
    state = {k: ({f: v for f, v in e.items() if f != "last_seen"} if isinstance(e, dict) else e) for k, e in load("state.json").items()}
    health = {r: {f: v for f, v in e.items() if f != "last_run"} for r, e in load("docs/health.json").items()}
    return json.dumps([state, load("docs/alerts.json"), health], sort_keys=True), json.dumps(load("docs/alerts.json"), sort_keys=True)


def run_cycle(number):
    import monitor
    import notify_new_listings
    import refresh_live_hits
    monitor.main(discover=number % DISCOVER_EVERY == 0)
    refresh_live_hits.main()
    notify_new_listings.main()


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
        if full != last_sig[0] and (alerts_changed or now() - last_commit >= commit_every):
            if commit():
                last_sig, last_commit = (full, alerts), now()
        if runtime and now() - start + interval > runtime:
            break
        sleep(max(1.0, interval - (now() - began)))
    commit()
    return number


if __name__ == "__main__":
    print(f"monitor loop: every {CYCLE_SECONDS:.0f}s for {RUNTIME_SECONDS:.0f}s (discovery every {DISCOVER_EVERY} cycles)")
    run_loop()
