"""Deterministic tests for retailer detection, alert safety, route integrity, and 30th coverage."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import timedelta

import monitor
import monitor_loop
import notify_new_listings
from monitor import classify_response, extract_structured_availability, is_pokemon, recently_stock_alerted, retailer_url_is_valid, should_prune, update_health


def test_retailer_urls():
    assert retailer_url_is_valid("target", "https://www.target.com/p/-/A-123456")
    assert retailer_url_is_valid("walmart", "https://www.walmart.com/ip/123456")
    assert retailer_url_is_valid("walmart", "https://business.walmart.com/ip/pokemon-destined-rivals/19965460207")
    assert retailer_url_is_valid("bestbuy", "https://www.bestbuy.com/product/example/JJG123")
    assert retailer_url_is_valid("gamestop", "https://www.gamestop.com/toys-games/trading-cards/products/example/123.html")
    assert not retailer_url_is_valid("gamestop", "https://pokemondb.net")


def test_pokemon_detection():
    assert is_pokemon("Pokemon 30th Anniversary Elite Trainer Box")
    assert is_pokemon("Mega Evolution Pitch Black ETB")
    assert not is_pokemon("Nike hoodie")


def test_structured_stock_signals():
    assert extract_structured_availability('"availability":"https://schema.org/InStock"') is True
    assert extract_structured_availability('"availability":"https://schema.org/OutOfStock"') is False
    assert extract_structured_availability('<html>unknown</html>') is None


def test_unknown_does_not_block_restock_cooldown():
    now = datetime.now(timezone.utc)
    legacy_unknown = {"last_alert": now.isoformat()}
    assert recently_stock_alerted(legacy_unknown, now, 1) is False
    verified_recently = {"last_stock_alert": now.isoformat()}
    assert recently_stock_alerted(verified_recently, now, 1) is True


def test_route_integrity():
    route = (ROOT / "docs" / "route.html").read_text(encoding="utf-8")
    stores = json.loads((ROOT / "docs" / "stores.json").read_text(encoding="utf-8"))["stores"]
    store_ids = {s["id"] for s in stores}
    end_match = re.search(r"const END_ID='([^']+)'", route)
    route_match = re.search(r"const ROUTE_IDS=\[(.*?)\];", route)
    assert end_match, "route terminal node missing"
    assert route_match, "route node list missing"
    ids = re.findall(r"'([^']+)'", route_match.group(1))
    ids.append(end_match.group(1))
    assert ids[-1] == "walmart-kearny"
    assert len(ids) == len(set(ids))
    assert all(i in store_ids for i in ids)
    assert len(ids) == 11
    assert "South Orange" not in route
    assert "exactPriority" not in route
    assert "withinIds" not in route
    assert "Open Route 1" in route and "Open Route 2" in route
    assert "Could not load the store list" in route


def test_store_pins_are_geocoded_and_plausible():
    stores = json.loads((ROOT / "docs" / "stores.json").read_text(encoding="utf-8"))["stores"]
    assert len(stores) >= 18
    for store in stores:
        lat, lng = store["lat"], store["lng"]
        assert 40.4 < lat < 41.2 and -74.5 < lng < -73.6, f"{store['id']} is outside the North Jersey / NYC area"
        # Hand-rounded pins like 40.76,-74.158 are what put markers hundreds of feet off the building.
        assert all(abs(round(v, 3) - v) > 1e-9 for v in (lat, lng)), f"{store['id']} has a rounded pin"
        assert store.get("pin_source", "").startswith("nominatim:"), f"{store['id']} pin was not verified against its address"
    assert len({s["id"] for s in stores}) == len(stores)


def test_pages_share_the_site_assets_and_have_no_broken_local_links():
    docs = ROOT / "docs"
    for page in ("index.html", "route.html", "30th.html"):
        html = (docs / page).read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="((?!https?:|#|mailto:|data:)[^"+\']+)"', html):  # skip hrefs built inside inline scripts
            target = docs / ref.split("?")[0].split("#")[0]
            assert target.exists(), f"{page} links to missing local file {ref}"
        assert "<title>" in html and 'name="viewport"' in html, page
        for other in {"index.html", "route.html", "30th.html"} - {page}:
            assert f'href="{other}"' in html, f"{page} does not link to {other}"
    css = (docs / "css" / "map.css").read_text(encoding="utf-8")
    closed = re.search(r"\.tag\.closed\s*\{([^}]*)\}", css).group(1)
    assert "var(--alert)" in closed and "muted" not in closed and "opacity" not in closed, "a closed store must be red, never grey or dimmed"
    assert "state-closed" in (docs / "js" / "map.js").read_text(encoding="utf-8"), "the popup must also say Closed in red"
    index = (docs / "index.html").read_text(encoding="utf-8")
    for needed in ('id="lamps"', 'id="live"', 'id="stores"', 'id="map"', "js/common.js", "js/map.js"):
        assert needed in index, f"index.html lost {needed}"


def test_30th_complete_coverage():
    data = json.loads((ROOT / "docs" / "30th_prices.json").read_text(encoding="utf-8"))
    guide = (ROOT / "docs" / "30th.html").read_text(encoding="utf-8")
    products = data["products"]
    ids = {p["id"] for p in products}
    required = {
        "etb", "pc-etb", "poster", "tech-lucario", "tech-exeggutor",
        "greninja-box", "sylveon-box", "knockout", "blister", "mini-tins",
        "binder", "booster-bundle", "battle-espeon", "battle-umbreon",
        "day-upc", "night-upc", "ditto", "mew-figure", "mewtwo-figure",
        "tin-sylveon", "tin-greninja",
    }
    assert len(products) == 21
    assert ids == required
    assert "full announced 30th Celebration lineup" in guide
    assert "Full buying list" in guide
    assert "tracked variants" in guide
    assert all(p["target_buy"] and p["max_buy"] is not None and p["score"] for p in products)


def test_classify_response():
    # The redirect target alone is enough, even when the wall page has innocent text.
    assert classify_response(200, "https://www.walmart.com/blocked?url=abc", "<html>Hello</html>") == (None, "blocked", None)
    # A bot wall that answers 200 after a redirect is UNKNOWN, never a reading (Walmart /blocked).
    assert classify_response(200, "https://www.walmart.com/blocked?url=abc", "<html>Robot or human?</html>") == (None, "blocked", None)
    assert classify_response(200, "https://www.target.com/p/-/A-1", "<div id='px-captcha'></div>") == (None, "blocked", None)
    assert classify_response(403, "https://www.gamestop.com/x", "") == (None, "blocked", None)
    assert classify_response(435, "https://www.target.com/x", "") == (None, "blocked", None)
    assert classify_response(429, "https://www.bestbuy.com/x", "") == (None, "blocked", None)
    assert classify_response(503, "https://www.bestbuy.com/x", "") == (None, "http_503", None)
    assert classify_response(None, "", "") == (None, "error", None)
    # Readings: structured data is strong, cart wording is only a weak text signal.
    assert classify_response(200, "https://x.com/p", '"availability":"https://schema.org/InStock"') == (True, "ok", "structured")
    assert classify_response(200, "https://x.com/p", '"availability":"https://schema.org/OutOfStock"') == (False, "ok", None)
    assert classify_response(200, "https://x.com/p", "<button>Add to cart</button>") == (True, "ok", "text")
    assert classify_response(200, "https://x.com/p", "Sold out online") == (False, "ok", None)
    assert classify_response(200, "https://x.com/p", "<html>nothing useful</html>") == (None, "no_signal", None)


def test_health_blind_spot_and_recovery():
    now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    blind = {"checked": 5, "readable": 0, "blocked": 5, "errors": 0}
    health = {}
    assert update_health(health, "walmart", blind, now) == []  # just started being blind
    assert update_health(health, "walmart", blind, now + timedelta(hours=23)) == []
    notices = update_health(health, "walmart", blind, now + timedelta(hours=25))
    assert len(notices) == 1 and notices[0][0] == "blind" and "25 h" in notices[0][2]
    assert update_health(health, "walmart", blind, now + timedelta(hours=26)) == []  # announced once, not every run
    back = update_health(health, "walmart", {"checked": 5, "readable": 3, "blocked": 2, "errors": 0}, now + timedelta(hours=27))
    assert [n[0] for n in back] == ["recovered"]
    assert "blind_since" not in health["walmart"]
    # A retailer that was never blind never announces anything, and an empty run is not "blind".
    assert update_health({}, "target", {"checked": 4, "readable": 4, "blocked": 0, "errors": 0}, now) == []
    assert update_health({}, "target", {"checked": 0, "readable": 0, "blocked": 0, "errors": 0}, now) == []


def test_prune_only_dead_discovered_listings():
    now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=9)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    dead = {"in_stock": None, "last_seen": old}
    assert should_prune(dead, False, now) is True
    assert should_prune(dead, True, now) is False  # seed URLs stay
    assert should_prune({"in_stock": None, "last_seen": fresh}, False, now) is False
    assert should_prune({"in_stock": None, "last_seen": old, "last_ok": fresh}, False, now) is False
    assert should_prune({"in_stock": False, "last_seen": fresh}, False, now) is False  # legacy but readable


class _Resp:
    def __init__(self, status, url, text):
        self.status_code, self.url, self.text = status, url, text


class _FakeSession:
    """Answers per retailer host; counts calls so a test can prove the skip logic."""
    def __init__(self, answers):
        self.answers, self.calls, self.headers = answers, [], {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        host = url.split("/")[2]
        for key, make in self.answers.items():
            if key in host:
                return make(url)
        return _Resp(404, url, "")


def _run_main(tmp, config, state, answers, discovered=(), discover=True):
    monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "health.json"
    monitor.CONFIG_FILE = tmp / "config.json"
    monitor.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")
    monitor.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    session = _FakeSession(answers)
    sent = []
    real_session, real_alert, real_discover = monitor.requests.Session, monitor.alert, monitor.discover_products
    monitor.requests.Session = lambda: session
    monitor.alert = lambda title, body, ping=False: sent.append((title, body))
    def fake_discover(http, retailer, keyword, timeout):
        session.discover_calls += 1
        return list(discovered)
    session.discover_calls = 0
    monitor.discover_products = fake_discover
    try:
        monitor.main(discover=discover)
    finally:
        monitor.requests.Session, monitor.alert, monitor.discover_products = real_session, real_alert, real_discover
    return session, sent


def test_main_blocked_retailer_is_silent_but_visible():
    import tempfile
    walmart_urls = [f"https://www.walmart.com/ip/{n}" for n in range(1, 9)]
    config = {"retailers": ["walmart"], "keywords": ["pokemon"], "seed_urls": {"walmart": walmart_urls}}
    blocked = lambda url: _Resp(200, "https://www.walmart.com/blocked?url=x", "<html>Robot or human?</html>")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        session, sent = _run_main(tmp, config, {"schema_version": 4}, {"walmart.com": blocked})
        health = json.loads((tmp / "health.json").read_text())["walmart"]
        state = json.loads((tmp / "state.json").read_text())
    assert sent == [], "a blocked retailer must never send a stock alert"
    assert health["checked"] == 8 and health["readable"] == 0 and health["blocked"] == 8 and "blind_since" in health
    assert all(v["in_stock"] is None and v["reason"] == "blocked" for k, v in state.items() if k != "schema_version")


def test_main_stops_probing_a_wall_but_keeps_reading_the_rest():
    import tempfile
    seeds = [f"https://www.walmart.com/ip/{n}" for n in (1, 2, 3)]
    extras = [f"https://www.walmart.com/ip/{n}" for n in range(10, 20)]
    config = {"retailers": ["walmart"], "keywords": ["pokemon"], "seed_urls": {"walmart": seeds}}
    blocked = lambda url: _Resp(200, "https://www.walmart.com/blocked?url=x", "<html>Robot or human?</html>")
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, {"schema_version": 4}, {"walmart.com": blocked}, discovered=extras)
    assert session.calls == seeds, "after 3 unreadable seeds nothing else should be requested"
    assert session.discover_calls == 0, "a retailer behind a wall is not even searched"
    # Fewer seeds than the limit: the extras are probed only until the streak is reached.
    config_one = {"retailers": ["walmart"], "keywords": ["pokemon"], "seed_urls": {"walmart": seeds[:1]}}
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config_one, {"schema_version": 4}, {"walmart.com": blocked}, discovered=extras)
    assert session.calls == seeds[:1] + extras[:2], session.calls
    # A retailer that answers is read in full, extras included.
    ok = lambda url: _Resp(200, url, "<title>Pokemon ETB</title>Sold out online")
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, {"schema_version": 4}, {"walmart.com": ok}, discovered=extras)
    assert session.calls == seeds + extras


def test_main_alert_wording_follows_signal_strength():
    import tempfile
    url = "https://www.target.com/p/-/A-1"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}, "map_url": "https://m"}
    prior = {"schema_version": 4, f"target::{url}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    cart = lambda u: _Resp(200, u, "<title>Pokemon ETB</title><button>Add to cart</button>")
    structured = lambda u: _Resp(200, u, '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"')
    for answer, expected in ((cart, "Likely in stock"), (structured, "Verified in stock")):
        with tempfile.TemporaryDirectory() as d:
            _, sent = _run_main(Path(d), config, dict(prior), {"target.com": answer})
        assert len(sent) == 1 and expected in sent[0][1], sent


class _Clock:
    """Fake monotonic clock: sleeping advances it, and a cycle can be told to take time."""
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        assert self.t < 5000, "the loop is not stopping at its runtime"


def test_loop_scheduling():
    clock = _Clock()
    ran, commits = [], []

    def cycle(n):
        ran.append(n)
        clock.t += 5  # each cycle takes 5 s
        if n == 3:
            raise RuntimeError("retailer exploded")

    count = monitor_loop.run_loop(cycle=cycle, commit=lambda: commits.append(clock.t) or True, sig=lambda: ("same", "[]"),
                                  now=clock.now, sleep=clock.sleep, runtime=600, interval=60, commit_every=300)
    assert count == len(ran) and ran == list(range(count)), "a failing cycle must not stop the watcher"
    assert 9 <= count <= 10, f"600 s at a 60 s interval should be about 10 cycles, got {count}"
    assert clock.t <= 600 + 1, "the loop must end inside its runtime so the next watcher can take over"
    assert len(commits) == 1, "nothing meaningful changed, so only the final commit attempt runs"


def test_loop_commits_on_meaningful_change_only():
    clock = _Clock()
    commits = []
    sigs = iter([("a", "[]"), ("a", "[]"), ("b", "[]"), ("b", "[]"), ("c", "[1]"), ("c", "[1]"), ("c", "[1]"), ("c", "[1]")])
    last = [("a", "[]")]

    def sig():
        last[0] = next(sigs, last[0])
        return last[0]

    monitor_loop.run_loop(cycle=lambda n: None, commit=lambda: commits.append(clock.t) or True, sig=sig,
                          now=clock.now, sleep=clock.sleep, runtime=240, interval=60, commit_every=300)
    # "b" differs but it is too soon after the last commit; the live-hit list changing ("c") commits at once; then the final attempt.
    assert len(commits) == 2 and commits[0] < 240, commits


def test_signature_ignores_last_seen():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "docs").mkdir()
        write = lambda seen: (root / "state.json").write_text(json.dumps({"target::u": {"in_stock": False, "last_seen": seen}}))
        (root / "docs/alerts.json").write_text("[]")
        (root / "docs/health.json").write_text("{}")
        old = monitor_loop.ROOT
        monitor_loop.ROOT = root
        try:
            write("t1"); first = monitor_loop.signature()
            write("t2"); assert monitor_loop.signature() == first, "last_seen moving is not a change"
            (root / "state.json").write_text(json.dumps({"target::u": {"in_stock": True, "last_seen": "t2"}}))
            assert monitor_loop.signature() != first, "a stock change is a change"
        finally:
            monitor_loop.ROOT = old


def test_fast_pass_does_not_search_and_rechecks_known_listings():
    import tempfile
    seed = "https://www.target.com/p/-/A-1"
    known = "https://www.target.com/p/-/A-2"
    stale = "https://www.target.com/p/-/A-3"
    now = datetime.now(timezone.utc)
    config = {"retailers": ["target"], "keywords": ["pokemon"], "seed_urls": {"target": [seed]}}
    entry = lambda ok: {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": now.isoformat(), "last_ok": ok}
    state = {"schema_version": 4, f"target::{known}": entry(now.isoformat()), f"target::{stale}": entry((now - timedelta(days=30)).isoformat())}
    sold_out = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, state, {"target.com": sold_out}, discovered=["https://www.target.com/p/-/A-9"], discover=False)
    assert session.discover_calls == 0, "the fast pass must not run keyword searches"
    assert session.calls == [seed, known], session.calls
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, state, {"target.com": sold_out}, discovered=["https://www.target.com/p/-/A-9"], discover=True)
    assert session.discover_calls == 1 and "https://www.target.com/p/-/A-9" in session.calls


def test_new_listing_announced_once_and_only_when_in_stock():
    import tempfile
    sent = []
    real_alert = notify_new_listings.alert
    notify_new_listings.alert = lambda title, body, ping=False: sent.append((title, body))
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00"}
    state = {
        "schema_version": 4,
        "target::https://www.target.com/p/-/A-1": {**base, "in_stock": True, "new_announced": False},
        "target::https://www.target.com/p/-/A-2": {**base, "in_stock": False, "new_announced": False},
        "target::https://www.target.com/p/-/A-3": {**base, "in_stock": None, "new_announced": False},
        "target::https://www.target.com/p/-/A-4": {**base, "in_stock": True},  # legacy entry: never new
        "target::https://www.target.com/p/-/A-5": {**base, "in_stock": True, "new_announced": False, "signal": "text"},
    }
    with tempfile.TemporaryDirectory() as d:
        notify_new_listings.STATE_FILE = Path(d) / "state.json"
        notify_new_listings.STATE_FILE.write_text(json.dumps(state))
        try:
            notify_new_listings.main()
            after = json.loads(notify_new_listings.STATE_FILE.read_text())
            notify_new_listings.main()  # a second run must not announce again
        finally:
            notify_new_listings.alert = real_alert
    assert len(sent) == 2, [s[0] for s in sent]
    assert "A-1" in sent[0][1] and "verified stock" in sent[0][1]
    assert "A-5" in sent[1][1] and "likely in stock" in sent[1][1]
    assert all(v.get("new_announced", True) for k, v in after.items() if k != "schema_version"), "every decided listing is marked, in stock or not"


def test_main_marks_new_listings_and_leaves_legacy_ones_alone():
    import tempfile
    seed, legacy = "https://www.target.com/p/-/A-1", "https://www.target.com/p/-/A-2"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [seed, legacy]}}
    state = {"schema_version": 4, f"target::{legacy}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": True, "last_seen": "2026-10-01T00:00:00+00:00", "last_stock_alert": "2026-10-01T00:00:00+00:00"}}
    cart = lambda u: _Resp(200, u, "<title>Pokemon ETB</title><button>Add to cart</button>")
    with tempfile.TemporaryDirectory() as d:
        _run_main(Path(d), config, state, {"target.com": cart})
        after = json.loads((Path(d) / "state.json").read_text())
    assert after[f"target::{seed}"]["new_announced"] is False, "a brand-new listing waits to be announced"
    assert after[f"target::{legacy}"]["new_announced"] is True, "a pre-existing listing must never be announced as new"
    assert after[f"target::{legacy}"]["first_seen"] == "2026-10-01T00:00:00+00:00"


if __name__ == "__main__":
    test_retailer_urls()
    test_pokemon_detection()
    test_structured_stock_signals()
    test_unknown_does_not_block_restock_cooldown()
    test_route_integrity()
    test_store_pins_are_geocoded_and_plausible()
    test_pages_share_the_site_assets_and_have_no_broken_local_links()
    test_30th_complete_coverage()
    test_classify_response()
    test_health_blind_spot_and_recovery()
    test_prune_only_dead_discovered_listings()
    test_main_blocked_retailer_is_silent_but_visible()
    test_main_stops_probing_a_wall_but_keeps_reading_the_rest()
    test_main_alert_wording_follows_signal_strength()
    test_loop_scheduling()
    test_loop_commits_on_meaningful_change_only()
    test_signature_ignores_last_seen()
    test_fast_pass_does_not_search_and_rechecks_known_listings()
    test_new_listing_announced_once_and_only_when_in_stock()
    test_main_marks_new_listings_and_leaves_legacy_ones_alone()
    print("accuracy, detection, health, route integrity, and 30th coverage tests passed")
