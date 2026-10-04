"""Make sure a stock watcher is running or queued; start one if not.

The watcher hands over to its own successor in its last step, but that step does not run when a job is killed outright (a lost runner, the
job timeout). GitHub's cron is the only other restart and it is best effort (measured median gap 239 minutes). The watchdog workflow runs this
whenever ANY monitor run ends, so a dead watcher is replaced within a minute.

A watcher is a monitor run started by schedule or workflow_dispatch; push and pull_request runs only run the tests. Exit 0 either way.
"""
from __future__ import annotations

import json
import subprocess
import sys

WATCHER_EVENTS = ("schedule", "workflow_dispatch")
LIVE_STATUSES = ("in_progress", "queued", "pending", "waiting", "requested")


def watcher_alive(runs):
    return any(r.get("event") in WATCHER_EVENTS and r.get("status") in LIVE_STATUSES for r in runs)


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def main() -> int:
    listed = gh("run", "list", "--workflow", "monitor.yml", "--limit", "30", "--json", "status,event")
    if listed.returncode != 0:
        print(f"could not list runs ({listed.stderr.strip()[:200]}); starting a watcher to be safe")
        runs = []
    else:
        runs = json.loads(listed.stdout or "[]")
    if watcher_alive(runs):
        print("a watcher is running or queued: nothing to do")
        return 0
    started = gh("workflow", "run", "monitor.yml", "--ref", "main")
    print("no watcher was running or queued: started one" if started.returncode == 0 else f"could not start a watcher: {started.stderr.strip()[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
