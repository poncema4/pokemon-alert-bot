"""Stamp every local script and stylesheet reference in docs/*.html with a content hash: js/common.js -> js/common.js?v=1a2b3c4d.

Why: GitHub Pages lets a browser cache a file for ten minutes. A browser holding the old common.js next to a new route.html calls functions
that file does not have and the page breaks. A new hash is a new URL, so a page and its scripts always come from the same release.

Run `python tools/stamp_assets.py` after changing anything in docs/js or docs/css; `--check` exits 1 if any reference is stale (the tests run it).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
REF = re.compile(r'(?P<attr>(?:src|href)=")(?P<path>(?:css|js)/[A-Za-z0-9_.-]+\.(?:css|js))(?:\?v=[0-9a-f]{8})?(?P<end>")')


def digest(path: str) -> str:
    return hashlib.sha256((DOCS / path).read_bytes()).hexdigest()[:8]


def stamped(html: str) -> str:
    return REF.sub(lambda m: f'{m["attr"]}{m["path"]}?v={digest(m["path"])}{m["end"]}', html)


def main(check: bool = False) -> int:
    stale = []
    for page in sorted(DOCS.glob("*.html")):
        old = page.read_text(encoding="utf-8")
        new = stamped(old)
        if new != old:
            stale.append(page.name)
            if not check:
                page.write_text(new, encoding="utf-8")
    if stale:
        print(("STALE: " if check else "stamped: ") + ", ".join(stale))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
