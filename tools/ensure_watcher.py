"""Make sure a stock watcher is running or queued; start one if not.

The watcher hands over to its own successor in its last step, but that step does not run when a job is killed outright (a lost runner, the
job timeout). GitHub's cron is the only other restart and it is best effort (measured median gap 239 minutes). The watchdog workflow runs this
whenever ANY monitor run ends, so a dead watcher is replaced within a minute.

A watcher is a monitor run started by schedule or workflow_dispatch; push and pull_request runs only run the tests. Exit 0 either way.

The same run also keeps an alarm, because "a run exists" is not "the watcher is working": for two weeks (Oct 5-19) every restart was started and
failed, the watchdog looked healthy, and nobody was told. The watcher commits docs/health.json every few minutes; when the newest last_run in it is
older than STALE_AFTER_MINUTES the alarm opens ONE GitHub issue (labelled watcher-down, which also emails the owner) and posts ONE Discord notice;
the open issue is the memory, so it never repeats. When the watcher reports again the issue is closed and a quiet "back" notice is posted.
`--drill` rehearses the whole path with a labelled DRILL issue and DRILL messages (no ping) so it can be proven live without an outage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH_FILE = ROOT / "docs" / "health.json"
STALE_AFTER_MINUTES = 25  # the watcher commits every ~5 min; a hand-over gap is a few minutes
DOWN_LABEL, DRILL_LABEL = "watcher-down", "watcher-drill"
USER_AGENT = "pokeping-watchdog/1.0 (personal project, github.com/poncema4/pokemon-alert-bot)"
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


def heartbeat_age_minutes(health, now):
    """Minutes since the newest last_run in the health record, or None when the record has none (damaged or empty: never alarm on bad data)."""
    newest = None
    for entry in (health.values() if isinstance(health, dict) else []):
        try:
            seen = datetime.fromisoformat(entry["last_run"])
        except (KeyError, TypeError, ValueError):
            continue
        if seen.tzinfo is not None and (newest is None or seen > newest):
            newest = seen
    return None if newest is None else (now - newest).total_seconds() / 60


def decide(age, down_open):
    """alarm: stale and nobody has been told yet; recover: reporting again while an alarm is open; none: everything else."""
    if age is None:
        return "none"
    if age >= STALE_AFTER_MINUTES:
        return "none" if down_open else "alarm"
    return "recover" if down_open else "none"


def notice(kind, minutes, repo, ping, drill=False, now=None):
    tag = "\U0001F9EA DRILL \u00b7 " if drill else ""
    link = f"https://github.com/{repo}/actions/workflows/monitor.yml" if repo else ""
    if kind == "alarm":
        title = f"{tag}\U0001F6A8 PokePing watcher is DOWN"
        text = f"No state heartbeat for {int(minutes)} minutes, so stock alerts may not be sending. Check the Actions page.\n{link}".strip()
        color = 0xE74C3C
    else:
        title = f"{tag}\u2705 PokePing watcher is back"
        text = "The watcher is reporting again."
        color = 0x2ECC71
    mention = bool(ping) and kind == "alarm" and not drill
    return {"username": "PokePing", "content": "@everyone" if mention else "", "allowed_mentions": {"parse": ["everyone"] if mention else []},
            "embeds": [{"title": title, "description": text, "color": color, "footer": {"text": "PokePing"}, "timestamp": (now or datetime.now(timezone.utc)).isoformat()}]}


def post_discord(url, payload):
    """Standard library only (this workflow installs nothing). Returns the HTTP status or None; never raises."""
    if not url:
        print("Discord: no DISCORD_WEBHOOK_URL secret, skip")
        return None
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(f"Discord: HTTP {response.status}")
            return response.status
    except urllib.error.HTTPError as exc:
        print(f"Discord: HTTP {exc.code}")
        return exc.code
    except Exception as exc:  # noqa: BLE001 - an alarm that crashes the watchdog is worse than a missed notice
        print(f"Discord send failed: {exc}")
        return None


def open_issues(label):
    listed = gh("issue", "list", "--label", label, "--state", "open", "--json", "number", "--limit", "5")
    if listed.returncode != 0:
        return None
    try:
        return [item["number"] for item in json.loads(listed.stdout or "[]")]
    except (ValueError, KeyError, TypeError):
        return None


def raise_alarm(label, minutes, repo, ping, drill, now):
    gh("label", "create", label, "--color", "d73a4a", "--description", "PokePing watcher alarm", "--force")
    title = f"{'[DRILL] ' if drill else ''}PokePing watcher is down (no heartbeat for {int(minutes)} min)"
    drill_line = "This is a drill: nothing is wrong.\n\n" if drill else ""
    body = (f"{drill_line}The newest last_run in docs/health.json is {int(minutes)} minutes old "
            f"(the watcher commits every ~5 minutes), so stock alerts may not be sending.\n\nCheck https://github.com/{repo}/actions/workflows/monitor.yml. "
            "This issue closes itself when the watcher reports again.")
    created = gh("issue", "create", "--title", title, "--body", body, "--label", label)
    if created.returncode != 0:
        print(f"could not open the alarm issue ({created.stderr.strip()[:200]}); not posting, so a retry cannot spam")
        return False
    post_discord(os.environ.get("DISCORD_WEBHOOK_URL", ""), notice("alarm", minutes, repo, ping, drill, now))
    return True


def clear_alarm(label, numbers, repo, drill, now):
    closed = [n for n in numbers if gh("issue", "close", str(n), "--comment", "The watcher is reporting again.").returncode == 0]
    if closed:
        post_discord(os.environ.get("DISCORD_WEBHOOK_URL", ""), notice("recover", 0, repo, False, drill, now))
    return bool(closed)


def heartbeat_alarm(drill=False, now=None, health=None):
    """Returns "alarm", "recover" or None. Never raises."""
    now = now or datetime.now(timezone.utc)
    repo = os.environ.get("GH_REPO", "")
    ping = os.environ.get("DISCORD_PING", "").strip().lower() == "true"
    label = DRILL_LABEL if drill else DOWN_LABEL
    try:
        if drill:
            age = STALE_AFTER_MINUTES + 5.0
        else:
            if health is None:
                health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            age = heartbeat_age_minutes(health, now)
        numbers = open_issues(label)
        if numbers is None:
            print("could not read the alarm issues; not alarming (an unreadable state must not cause repeats)")
            return None
        action = decide(age, bool(numbers))
        if action == "alarm" and raise_alarm(label, age, repo, ping, drill, now):
            if drill:  # rehearsal: close it again straight away, which also proves the "back" path
                clear_alarm(label, open_issues(label) or [], repo, drill, now)
            return "alarm"
        if action == "recover" and clear_alarm(label, numbers, repo, drill, now):
            return "recover"
        print(f"watcher heartbeat: {'unknown' if age is None else f'{age:.0f} min old'}; nothing to do")
    except Exception as exc:  # noqa: BLE001 - the watchdog's restart job must not fail because the alarm did
        print(f"the heartbeat alarm failed: {exc}")
    return None


if __name__ == "__main__":
    drill = "--drill" in sys.argv
    code = 0 if drill else main()  # a drill only rehearses the alarm; it never starts a watcher
    heartbeat_alarm(drill=drill)
    sys.exit(code)
