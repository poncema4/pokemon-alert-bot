"""The watcher's heartbeat commit must survive the price-refresh workflow pushing the same file in the meantime.

Real git, no fakes: a bare "GitHub", the watcher's clone, and the refresh workflow's clone. The refresh pushes a new
docs/30th_prices.json while the watcher has its own change to the same lines plus a state change. The old code ran a plain
`git pull --rebase`, which stopped on the conflict and left the rebase half done, so every later heartbeat of the run failed.

Run: python -B tests/test_commit_conflict.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
import monitor_loop  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        remote, watcher, refresh = tmp / "remote.git", tmp / "watcher", tmp / "refresh"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(remote))
        git(tmp, "clone", "-q", str(remote), str(watcher))
        for repo in (watcher,):
            git(repo, "config", "user.name", "t")
            git(repo, "config", "user.email", "t@t")
        (watcher / "docs").mkdir()
        (watcher / "data").mkdir()
        (watcher / "docs" / "30th_prices.json").write_text('{\n  "etb": 50\n}\n')
        (watcher / "data" / "state.json").write_text('{"n": 0}\n')
        git(watcher, "add", "-A")
        git(watcher, "commit", "-q", "-m", "init")
        git(watcher, "push", "-q", "origin", "main")
        git(tmp, "clone", "-q", str(remote), str(refresh))
        git(refresh, "config", "user.name", "t")
        git(refresh, "config", "user.email", "t@t")

        # the price-refresh workflow lands first
        (refresh / "docs" / "30th_prices.json").write_text('{\n  "etb": 61\n}\n')
        git(refresh, "commit", "-q", "-am", "Refresh prices")
        git(refresh, "push", "-q", "origin", "main")

        # the watcher, still on the old copy, changes the same line and its own state
        (watcher / "docs" / "30th_prices.json").write_text('{\n  "etb": 55\n}\n')
        (watcher / "data" / "state.json").write_text('{"n": 1}\n')

        monitor_loop.ROOT = watcher
        monitor_loop.TRACKED = ("data/state.json", "docs/30th_prices.json")
        ok = monitor_loop.commit_and_push()

        assert ok is True, "the heartbeat push failed on a conflict"
        assert not (watcher / ".git" / "rebase-merge").exists() and not (watcher / ".git" / "rebase-apply").exists(), "left mid-rebase"
        assert git(refresh, "pull", "-q", "--rebase") is not None
        assert '"n": 1' in (refresh / "data" / "state.json").read_text(), "the watcher's state did not reach the remote"
        assert '"etb": 61' in (refresh / "docs" / "30th_prices.json").read_text(), "the fresher refreshed prices were overwritten"

        # and the NEXT heartbeat still works (the old failure repeated forever)
        (watcher / "data" / "state.json").write_text('{"n": 2}\n')
        assert monitor_loop.commit_and_push() is True, "the next heartbeat failed"
        git(refresh, "pull", "-q", "--rebase")
        assert '"n": 2' in (refresh / "data" / "state.json").read_text()
    print("ok: heartbeat survives a conflicting price refresh, and keeps working afterwards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
