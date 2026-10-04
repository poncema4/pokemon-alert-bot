"""Deterministic tests for retailer detection, alert safety, route integrity, and 30th coverage."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "bot") not in sys.path:
    sys.path.insert(0, str(ROOT / "bot"))

from datetime import timedelta

import monitor
monitor.CONFIRM_DELAY = 0  # tests never wait
import monitor_loop
import advisor
import browser_reader
import coverage
import gamestop_discovery
import notify
import notify_new_listings
import refresh_live_hits
import update_30th_prices as prices_mod
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
        assert store.get("pin_source", "").startswith(("nominatim:", "osm-poi:")), f"{store['id']} pin was not verified against OpenStreetMap"
    assert len({s["id"] for s in stores}) == len(stores)
    by_id = {s["id"]: s for s in stores}

    def feet(a, b):
        import math
        p = math.radians
        x = math.sin(p(b[0] - a[0]) / 2) ** 2 + math.cos(p(a[0])) * math.cos(p(b[0])) * math.sin(p(b[1] - a[1]) / 2) ** 2
        return 2 * 3958.8 * math.asin(math.sqrt(x)) * 5280
    # GameStop Kearny's street address geocodes onto the Taco Bell next door; the pin must sit on the GameStop's own OpenStreetMap record.
    gamestop = (by_id["gamestop-kearny"]["lat"], by_id["gamestop-kearny"]["lng"])
    assert feet(gamestop, (40.748610, -74.135150)) < 60, "GameStop Kearny must sit on the GameStop record"
    assert feet(gamestop, (40.749201, -74.134908)) > 150, "...and not on the Taco Bell the address resolves to"
    for chain in ("target", "walmart", "bestbuy", "gamestop"):
        for store in stores:
            if store["retailer"] == chain and store["id"] != "gamestop-lyndhurst":
                assert store["pin_source"].startswith("osm-poi:"), f"{store['id']} should use the chain's own OpenStreetMap record"


def _page_html(name):
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


PAGES = {"index.html": ("PokePing · Map", "Map"), "etbs.html": ("PokePing · ETBs", "ETBs"), "route.html": ("PokePing · Route", "Route"), "30th.html": ("PokePing · 30th guide", "30th guide")}


def test_pages_share_one_shell_and_have_no_broken_local_links():
    docs = ROOT / "docs"
    headers = []
    for page, (title, label) in PAGES.items():
        html = _page_html(page)
        for ref in re.findall(r'(?:href|src)="((?!https?:|#|mailto:|data:)[^"+\']+)"', html):  # skip hrefs built inside inline scripts
            assert (docs / ref.split("?")[0].split("#")[0]).exists(), f"{page} links to missing local file {ref}"
        assert f"<title>{title}</title>" in html, f"{page} title must be {title!r}: short, same name, only the page changes"
        assert 'rel="icon" href="favicon.svg"' in html, f"{page} needs the pokéball tab icon"
        assert 'name="viewport"' in html and 'name="description"' in html and "css/site.css" in html
        header = re.search(r'<header class="topbar">.*?</header>', html, re.S).group(0)
        assert header.count('aria-current="page"') == 1 and f'aria-current="page">{label}</a>' in header, f"{page} must mark only itself as current"
        assert '<a class="brand" href="index.html"><img src="favicon.svg"' in header and "<span>PokePing</span>" in header
        assert 'id="lamps"' in header, f"{page}: the status lamps belong in the top bar of every page"
        headers.append(re.sub(r"\s+", "", header.replace(' aria-current="page"', "")))
    assert len(set(headers)) == 1, "the header and nav must be identical on every page"
    forbidden = ("Set your home", "Set home", "town centre", "restock-radar-home", "localStorage", "Restock Radar")
    for page in list(PAGES) + ["js/map.js", "js/common.js", "css/map.css"]:
        text = _page_html(page)
        assert not [w for w in forbidden if w in text], f"{page} still mentions removed UI: {[w for w in forbidden if w in text]}"


def test_repo_stays_organised_and_the_readme_matches_it():
    root_files = {p.name for p in ROOT.iterdir() if p.is_file()}
    assert root_files <= {"README.md", "requirements.txt", ".gitignore"}, f"loose files at the repo root: {sorted(root_files - {'README.md', 'requirements.txt', '.gitignore'})}"
    for folder in ("bot", "config", "data", "docs", "tools", "tests", ".github/workflows"):
        assert (ROOT / folder).is_dir(), folder
    assert not list(ROOT.glob("*.py")) and not list(ROOT.glob("*.json")), "code belongs in bot/ or tools/, data in data/ or config/"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for workflow in re.findall(r"`([a-z0-9-]+\.yml)`", readme):
        assert (ROOT / ".github" / "workflows" / workflow).exists(), f"README names a workflow that does not exist: {workflow}"
    for module in re.findall(r"`(bot/[a-z_0-9]+\.py)`", readme) + re.findall(r"`(tools/[a-z_0-9]+\.py)`", readme):
        assert (ROOT / module).exists(), f"README names a file that does not exist: {module}"
    for line in re.findall(r"^(bot|config|data|docs|tools|tests)/", readme, re.M):
        assert (ROOT / line).is_dir()
    for needed in ("data/state.json", "data/ground_truth.json", "config/search_config.json"):
        assert (ROOT / needed).exists(), f"{needed} (gathered data / config) must never go missing"
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    assert len(state) > 5, "state.json is the bot's memory: it must not be emptied by a reorganisation"
    # Every code file the workflows run lives where the workflows say it does.
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for script in re.findall(r"python -u ((?:bot|tools)/[a-z_0-9]+\.py)", workflow.read_text(encoding="utf-8")):
            assert (ROOT / script).exists(), f"{workflow.name} runs {script}, which does not exist"


def test_the_name_is_always_PokePing():
    """One spelling everywhere: PokePing (two capital P's). Only the repository name stays pokemon-alert-bot."""
    wrong = re.compile(r"(?i)pok[eé]\s?ping(?<!PokePing)")
    checked = 0
    for path in list((ROOT / "docs").rglob("*")) + list(ROOT.glob("*.py")) + list(ROOT.glob("*.md")) + list((ROOT / "tools").glob("*.py")) + list((ROOT / ".github").rglob("*.yml")):
        if path.suffix not in (".html", ".js", ".css", ".py", ".md", ".yml", ".json", ".svg") or not path.is_file() or "fixtures" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in wrong.finditer(text):
            assert False, f"{path.relative_to(ROOT)} spells the name {m.group(0)!r}; it must be PokePing"
        checked += 1
    assert checked > 10
    assert notify.BOT_NAME == "PokePing"
    for page, (title, _label) in PAGES.items():
        assert title.startswith("PokePing"), page
        assert "<span>PokePing</span>" in _page_html(page)
    assert "Restock Radar" not in _page_html("index.html") and "Pokémon Restock Radar" not in _page_html("index.html"), "the long old name is gone"
    assert notify.stock_embed(_card())["embeds"][0]["footer"]["text"].startswith("PokePing ·")


def test_home_is_hard_set_and_distances_use_it():
    home = json.loads((ROOT / "docs" / "stores.json").read_text(encoding="utf-8"))["home"]
    stores = {s["id"]: s for s in json.loads((ROOT / "docs" / "stores.json").read_text(encoding="utf-8"))["stores"]}

    def miles(a, b, c, d):
        import math
        p = math.radians
        x = math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2
        return 2 * 3958.8 * math.asin(math.sqrt(x))
    assert miles(home["lat"], home["lng"], 40.797211, -74.125219) < 0.02, "home must be the geocoded address point"
    assert home["pin_source"].startswith("us-census-geocoder:") and home["name"] == "Home"
    # Independent distances (computed by hand from the pins): the nearest stores are the Lyndhurst shops, Target Kearny is farther.
    assert 0.8 < miles(home["lat"], home["lng"], stores["east-coast-connection"]["lat"], stores["east-coast-connection"]["lng"]) < 1.1
    assert 2.6 < miles(home["lat"], home["lng"], stores["target-kearny"]["lat"], stores["target-kearny"]["lng"]) < 3.2
    assert "40.7884" not in (ROOT / "docs" / "js" / "map.js").read_text(encoding="utf-8") + _page_html("route.html"), "the old town-centre pin must be gone everywhere"


def test_phone_rules_on_every_page():
    site = (ROOT / "docs" / "css" / "site.css").read_text(encoding="utf-8")
    phone_site = site[site.index("@media (max-width: 800px)"):]
    assert "position: fixed" in phone_site and "bottom: 0" in phone_site, "phones need the bottom tab bar"
    assert "min-height: 44px" in phone_site, "tap targets must be at least 44 px on phones"
    css = (ROOT / "docs" / "css" / "map.css").read_text(encoding="utf-8")
    closed = re.search(r"\.tag\.closed\s*\{([^}]*)\}", css).group(1)
    assert "var(--alert)" in closed and "muted" not in closed and "opacity" not in closed, "a closed store must be red, never grey or dimmed"
    assert "state-closed" in (ROOT / "docs" / "js" / "map.js").read_text(encoding="utf-8"), "the popup must also say Closed in red"
    assert re.search(r"\.rail\s*\{[^}]*position:\s*relative", css), "the rail must contain its .sr-only labels or the whole page scrolls on phones"
    phone_map = css[css.index("@media (max-width: 800px)"):]
    assert "min-height: 64px" in phone_map and "overflow-x: auto" in phone_map and ".hit { min-height: 64px" in phone_map and ".store { min-height: 64px" in phone_map, "live rows and store rows share one size"
    guide = (ROOT / "docs" / "css" / "guide.css").read_text(encoding="utf-8")
    assert "font-size: 16px" in guide[guide.index("@media (max-width: 800px)"):], "inputs under 16 px make iOS zoom the page on focus"
    route_css = (ROOT / "docs" / "css" / "route.css").read_text(encoding="utf-8")
    assert re.search(r"\.status-text\.closed\s*\{[^}]*background:\s*var\(--alert\)", route_css) and re.search(r"\.closed-stat b\s*\{[^}]*color:\s*#ff6b61", route_css), "closed stops on the route page are solid red too"
    assert "grid-template-columns: repeat(2, 1fr)" in route_css[route_css.index("@media (max-width: 800px)"):]


def test_every_page_script_is_valid_javascript():
    import shutil
    import subprocess
    if not shutil.which("node"):
        return  # CI has node; a laptop without it skips this one check
    for page in ("route.html", "30th.html"):
        script = re.search(r"<script>\n(.*?)</script>", _page_html(page), re.S).group(1)
        result = subprocess.run(["node", "--check", "-"], input=script, capture_output=True, text=True)
        assert result.returncode == 0, f"{page} inline script has a syntax error: {result.stderr[:200]}"
    for js in ("common.js", "map.js"):
        result = subprocess.run(["node", "--check", str(ROOT / "docs" / "js" / js)], capture_output=True, text=True)
        assert result.returncode == 0, f"{js}: {result.stderr[:200]}"


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
    for needed in ("Buying list", "Chase cards", "tracked variants", 'id="products"', 'id="hits"', 'id="wave"', 'id="updated"'):
        assert needed in guide, f"30th.html lost {needed}"
    assert 'value="2026-' not in guide, "release waves come from the data, never hard-coded"
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
    # Target's real server-rendered placeholder (captured 2026-10-04): a DISABLED cart button is a loading state, not stock.
    target_placeholder = '<div><button class="styles_md__N9Usy styles_filled__uq68y styles_fullWidth__ztP_d" type="button" disabled="">Add to cart</button></div>'
    assert classify_response(200, "https://www.target.com/p/-/A-1", target_placeholder) == (None, "cart_disabled", None)
    assert classify_response(200, "https://www.target.com/p/-/A-1", '<button type="button" disabled>Add to cart</button>') == (None, "cart_disabled", None)
    assert classify_response(200, "https://x.com/p", '<button type="button" class="go">Add to cart</button>') == (True, "ok", "text"), "an enabled button still counts"
    assert classify_response(200, "https://x.com/p", "<span>Pickup today at your store</span>") == (True, "ok", "text"), "wording with no cart button at all is still a weak in-stock signal"
    assert classify_response(200, "https://x.com/p", "<a>Add to cart</a>") == (True, "ok", "text"), "only a disabled <button> is a placeholder"
    mixed = '<button disabled>Add to cart</button><button class="x">Add to cart</button>'
    assert classify_response(200, "https://x.com/p", mixed) == (True, "ok", "text"), "any enabled cart button wins"
    assert classify_response(200, "https://x.com/p", '<button disabled>Add to cart</button> Ship it') == (None, "cart_disabled", None), "a disabled button is not rescued by other wording on the page"
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


def _fake_tcg_search(query):
    """Offline stand-in for the TCGplayer search: answers from captured real responses."""
    q = query.lower()
    if "pitch black" in q:
        return _fixture("tcg_search_pitch_black.json")
    if "phantasmal" in q:
        return _fixture("tcg_search_phantasmal.json")
    return {"results": [{"totalResults": 0, "results": []}]}


def _run_main(tmp, config, state, answers, discovered=(), discover=True, cycle=0):
    monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE, monitor.MARKET_FILE, monitor.PIDS_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "health.json", tmp / "market.json", tmp / "pids.json"
    monitor.CONFIG_FILE = tmp / "config.json"
    monitor.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")
    monitor.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    session = _FakeSession(answers)
    sent = []  # stock alerts arrive as card dicts, notices as (title, body) tuples
    real_session, real_alert, real_card, real_discover, real_fetch = monitor.requests.Session, monitor.alert, monitor.send_card, monitor.discover_products, advisor.fetch_query
    monitor.requests.Session = lambda: session
    monitor.alert = lambda title, body, ping=False, tone="blind": sent.append((title, body))
    monitor.send_card = lambda card: sent.append(card)
    advisor.fetch_query = _fake_tcg_search
    def fake_discover(http, retailer, keyword, timeout):
        session.discover_calls += 1
        return list(discovered)
    session.discover_calls = 0
    monitor.discover_products = fake_discover
    try:
        monitor.main(discover=discover, cycle=cycle)
    finally:
        monitor.requests.Session, monitor.alert, monitor.send_card, monitor.discover_products, advisor.fetch_query = real_session, real_alert, real_card, real_discover, real_fetch
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


def test_main_target_placeholder_never_becomes_stock():
    import tempfile
    url = "https://www.target.com/p/-/A-1"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}}
    prior = {"schema_version": 4, f"target::{url}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    placeholder = lambda u: _Resp(200, u, '<title>Pokemon ETB : Target</title><button type="button" disabled="">Add to cart</button>')
    with tempfile.TemporaryDirectory() as d:
        _, sent = _run_main(Path(d), config, prior, {"target.com": placeholder})
        state = json.loads((Path(d) / "state.json").read_text())[f"target::{url}"]
        health = json.loads((Path(d) / "health.json").read_text())["target"]
    assert sent == [] and state["in_stock"] is None and state["reason"] == "cart_disabled"
    assert health["readable"] == 0 and health["checked"] == 1, "Target must show up as unreadable, not as quietly fine"


def test_main_alert_wording_follows_signal_strength():
    import tempfile
    url = "https://www.target.com/p/-/A-1"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}, "map_url": "https://m"}
    prior = {"schema_version": 4, f"target::{url}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    cart = lambda u: _Resp(200, u, "<title>Pokemon ETB</title><button>Add to cart</button>")
    structured = lambda u: _Resp(200, u, '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"')
    for answer, expected in ((cart, "Likely"), (structured, "Verified")):
        with tempfile.TemporaryDirectory() as d:
            _, sent = _run_main(Path(d), config, dict(prior), {"target.com": answer})
        assert len(sent) == 1 and expected in notify.stock_embed(sent[0])["embeds"][0]["fields"][3]["value"], sent


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
    assert len(commits) in (2, 3) and 295 <= commits[0] <= 360, f"nothing changed, yet a heartbeat commit runs about every 300 s, then the final one: {commits}"


def test_cycle_refreshes_prices_on_schedule_and_survives_a_price_failure():
    import notify_new_listings as nn
    import refresh_live_hits as rl
    order = []
    real = (monitor.main, rl.main, nn.main, prices_mod.main, coverage.main, monitor_loop.refresh_market)
    coverage.main = lambda: None
    monitor_loop.refresh_market = lambda: None
    monitor.main = lambda discover=True, cycle=0: order.append(("stock", discover))
    rl.main = lambda: order.append("hits")
    nn.main = lambda: order.append("new")
    state = {"fail": False}

    def price_main():
        order.append("prices")
        if state["fail"]:
            raise RuntimeError("tcgplayer down")

    prices_mod.main = price_main
    try:
        monitor_loop.run_cycle(0)
        assert order == ["prices", ("stock", True), "hits", "new"], order
        order.clear()
        monitor_loop.run_cycle(1)
        assert "prices" not in order and ("stock", False) in order, "prices refresh only every PRICE_EVERY cycles, and the fast pass skips discovery"
        order.clear()
        state["fail"] = True
        monitor_loop.run_cycle(monitor_loop.PRICE_EVERY)
        assert order == ["prices", ("stock", monitor_loop.PRICE_EVERY % monitor_loop.DISCOVER_EVERY == 0), "hits", "new"], "a failing price refresh must not stop the stock checks"
    finally:
        monitor.main, rl.main, nn.main, prices_mod.main, coverage.main, monitor_loop.refresh_market = real


def test_the_loop_commits_a_heartbeat_even_when_nothing_changed():
    """When every store is out of stock nothing 'meaningful' changes; the committed last_seen / last_run still has to move or the site says
    'checked 38 minutes ago' for a watcher that is running fine."""
    clock = _Clock()
    commits = []
    monitor_loop.run_loop(cycle=lambda n: None, commit=lambda: commits.append(clock.t) or True, sig=lambda: ("same", "[]"),
                          now=clock.now, sleep=clock.sleep, runtime=1500, interval=30, commit_every=300)
    beats = commits[:-1]   # the last one is the final commit at shutdown
    assert len(beats) >= 4, f"a 25 minute run with nothing changing must still commit about every 5 minutes, got {commits}"
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert all(295 <= g <= 335 for g in gaps), f"heartbeats about every 300 s (not faster: every commit rebuilds the Pages site), got gaps {gaps}"
    assert commits[0] <= 335, "the first heartbeat arrives within about five minutes of the start"


def test_a_failed_commit_attempt_does_not_retry_every_cycle():
    clock = _Clock()
    attempts = []
    monitor_loop.run_loop(cycle=lambda n: None, commit=lambda: attempts.append(clock.t) or False, sig=lambda: ("same", "[]"),
                          now=clock.now, sleep=clock.sleep, runtime=900, interval=30, commit_every=300)
    assert len(attempts) <= 4, f"nothing to commit must not mean git runs after every 30 s cycle: {attempts}"


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
    # "b" differs but it is too soon after the last commit (the heartbeat is 300 s); the live-hit list changing ("c") commits at once; then the final attempt.
    assert len(commits) == 2 and commits[0] < 240, commits


def test_signature_ignores_last_seen():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "docs").mkdir()
        (root / "data").mkdir()
        write = lambda seen: (root / "data" / "state.json").write_text(json.dumps({"target::u": {"in_stock": False, "last_seen": seen}}))
        (root / "docs/alerts.json").write_text("[]")
        (root / "docs/health.json").write_text("{}")
        old = monitor_loop.ROOT
        monitor_loop.ROOT = root
        try:
            write("t1"); first = monitor_loop.signature()
            write("t2"); assert monitor_loop.signature() == first, "last_seen moving is not a change"
            (root / "data" / "state.json").write_text(json.dumps({"target::u": {"in_stock": True, "last_seen": "t2"}}))
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


def test_one_alert_per_stay_in_stock_and_rearming_needs_confirmed_out_of_stock():
    """Marco: do not ping again and again for the same ETB. A flickering page (in stock / blocked / in stock) is still one stay."""
    import tempfile
    from datetime import timedelta
    url = "https://www.target.com/p/-/A-1"
    key = f"target::{url}"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}}
    in_stock = lambda u: _Resp(200, u, '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"')
    sold_out = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")
    blocked = lambda u: _Resp(403, u, "Access denied")
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-03T00:00:00+00:00", "in_stock": False}

    def run(tmp, answer, mutate=None):
        state = json.loads((tmp / "state.json").read_text()) if (tmp / "state.json").exists() else {"schema_version": 4, key: dict(base)}
        if mutate:
            mutate(state[key])
        _, sent = _run_main(tmp, config, state, {"target.com": answer})
        return [c for c in sent if isinstance(c, dict)], json.loads((tmp / "state.json").read_text())[key]

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cards, entry = run(tmp, in_stock)
        assert len(cards) == 1 and entry["armed"] is False, "the first move to in stock alerts, then disarms"
        cards, entry = run(tmp, blocked)
        assert cards == [] and entry["in_stock"] is None and entry["armed"] is False, "an unknown reading does not re-arm"
        cards, entry = run(tmp, in_stock)
        assert cards == [], "in stock again after an unknown reading is the SAME stay: no second ping"
        cards, entry = run(tmp, in_stock)
        assert cards == [] and entry["armed"] is False
        cards, entry = run(tmp, sold_out)
        assert cards == [] and entry["out_since"] and entry["armed"] is False, "out of stock starts the streak but does not arm at once"
        cards, entry = run(tmp, in_stock)
        assert cards == [], "a sell-out blip of seconds is not a new stay"
        # a confirmed out-of-stock streak that has lasted 20+ minutes re-arms; then the next restock alerts again
        long_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        cards, entry = run(tmp, sold_out, mutate=lambda e: e.update({"out_since": long_ago}))
        assert cards == [] and entry["armed"] is True, "30 minutes of confirmed out-of-stock re-arms"
        cards, entry = run(tmp, in_stock)
        assert len(cards) == 1 and entry["armed"] is False, "a real restock after a real sell-out alerts again, once"
        # unknown readings in the middle of an out-of-stock streak never count towards re-arming
        cards, entry = run(tmp, blocked, mutate=lambda e: e.update({"in_stock": False, "armed": False, "out_since": datetime.now(timezone.utc).isoformat()}))
        assert entry["armed"] is False


def test_rearm_state_rules():
    now = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
    assert monitor.rearm_state(None, False, now, 20) == (True, now.isoformat()), "no history: armed (the first alert is allowed)"
    assert monitor.rearm_state({"armed": False}, None, now, 20) == (False, None), "unknown changes nothing"
    assert monitor.rearm_state({"armed": False, "out_since": "2026-10-04T17:50:00+00:00"}, False, now, 20) == (False, "2026-10-04T17:50:00+00:00"), "10 minutes out is not enough"
    assert monitor.rearm_state({"armed": False, "out_since": "2026-10-04T17:40:00+00:00"}, False, now, 20)[0] is True, "exactly 20 minutes out re-arms"
    assert monitor.rearm_state({"armed": False, "out_since": "2026-10-04T17:30:00+00:00"}, True, now, 20) == (False, None), "back in stock ends the streak"
    assert monitor.rearm_state({"armed": False, "out_since": "garbage"}, False, now, 20) == (False, now.isoformat()), "a corrupt timestamp restarts the streak, never crashes"


def test_a_marketplace_reseller_is_not_the_store_restocking():
    """Measured 2026-10-04: every Best Buy 'in stock' reading (Chaos Rising, Perfect Order, Pitch Black) was a third-party seller
    ('Sold & shipped by Shopville Inc / Collectors Emporium', 'More options from Marketplace sellers $94.99 - $155.94'), not Best Buy."""
    import sellers
    bb = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-chaos-rising-elite-trainer-box/JJG2TL34RT"
    assert sellers.is_store_seller(bb, "Best Buy") and sellers.is_store_seller(bb, "Best Buy Marketplace Direct") is True
    assert not sellers.is_store_seller(bb, "Shopville Inc") and not sellers.is_store_seller(bb, "Collectors Emporium")
    assert sellers.is_store_seller(bb, None) and sellers.is_store_seller(bb, ""), "no seller named: a first-party restock must never be missed"
    assert sellers.is_store_seller("https://example.test/p/1", "Anyone"), "a store we do not know is not filtered"
    assert sellers.marketplace_only(bb, ["Shopville Inc"]) and sellers.marketplace_only(bb, ["Shopville Inc", "Collectors Emporium"])
    assert not sellers.marketplace_only(bb, ["Shopville Inc", "Best Buy"]) and not sellers.marketplace_only(bb, []), "any first-party offer, or no seller info, is not marketplace-only"
    assert sellers.json_sellers('"offers":{"seller":{"@type":"Organization","name":"GameStop"},"price":"84.99"}') == ["GameStop"]
    buy = {"label": "Add to cart", "visible": True, "enabled": True}
    page = {"url": bb, "title": "Pokemon ETB - Best Buy", "body_chars": 3300, "walls": [], "buttons": [buy]}
    assert browser_reader.classify_rendered({**page, "seller": "Shopville Inc"}) == (False, "marketplace_only", None)
    assert browser_reader.classify_rendered({**page, "seller": "Collectors Emporium"}) == (False, "marketplace_only", None)
    assert browser_reader.classify_rendered({**page, "seller": "Best Buy"}) == (True, "ok", "browser")
    assert browser_reader.classify_rendered(page) == (True, "ok", "browser"), "no seller named: still a restock"
    # HTTP stores: the page's own seller data
    def read(retailer, url, seller, available="InStock"):
        body = f'<title>Pokemon ETB</title><script type="application/ld+json">{{"@type":"Product","offers":{{"availability":"https://schema.org/{available}","price":"96.99","seller":{{"@type":"Organization","name":"{seller}"}}}}}}</script>'
        return monitor.check_product_page(_FakeSession({url.split("/")[2].replace("www.", ""): lambda u: _Resp(200, u, body)}), retailer, url, 5)
    for retailer, url, own in (("target", "https://www.target.com/p/-/A-1", "Target"), ("walmart", "https://www.walmart.com/ip/1", "Walmart.com"), ("gamestop", "https://www.gamestop.com/toys-games/trading-cards/products/x/1.html", "GameStop")):
        mine, other = read(retailer, url, own), read(retailer, url, "Some Reseller LLC")
        assert mine["stock"] is True and mine["reason"] == "ok", f"{retailer}: sold by the store itself is in stock"
        assert other["stock"] is False and other["reason"] == "marketplace_only" and other["seller"] == "Some Reseller LLC", f"{retailer}: a reseller is not the store restocking"
        assert read(retailer, url, "Some Reseller LLC", available="OutOfStock")["stock"] is False
    # the real captured GameStop page names GameStop as the seller and stays in stock
    gs = (ROOT / "tests" / "fixtures" / "pages" / "gamestop_pitch_black_etb_available.html").read_text(encoding="utf-8")
    assert monitor.check_product_page(_FakeSession({"gamestop.com": lambda u: _Resp(200, u, gs)}), "gamestop", "https://www.gamestop.com/toys-games/trading-cards/products/x/445744.html", 5)["stock"] is True


def test_a_reseller_listing_never_alerts_and_clears_a_stale_in_stock_state():
    import tempfile
    url = "https://www.target.com/p/-/A-1"
    key = f"target::{url}"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}}
    reseller = lambda u: _Resp(200, u, '<title>Pokemon ETB</title><script type="application/ld+json">{"@type":"Product","offers":{"availability":"https://schema.org/InStock","price":"96.99","seller":{"@type":"Organization","name":"Collectors Emporium"}}}</script>')
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-03T00:00:00+00:00"}
    with tempfile.TemporaryDirectory() as d:
        _, sent = _run_main(Path(d), config, {"schema_version": 4, key: {**base, "in_stock": False}}, {"target.com": reseller})
        entry = json.loads((Path(d) / "state.json").read_text())[key]
    assert [c for c in sent if isinstance(c, dict)] == [] and entry["in_stock"] is False and entry["reason"] == "marketplace_only" and entry["seller"] == "Collectors Emporium"
    with tempfile.TemporaryDirectory() as d:   # an item an older version wrongly called in stock is corrected on the next read
        _, sent = _run_main(Path(d), config, {"schema_version": 4, key: {**base, "in_stock": True, "in_stock_since": "2026-10-04T10:00:00+00:00"}}, {"target.com": reseller})
        entry = json.loads((Path(d) / "state.json").read_text())[key]
    assert entry["in_stock"] is False and entry["in_stock_since"] is None, "the false in-stock state is cleared"


def test_a_new_listing_alert_uses_up_that_stay():
    import tempfile
    sent = []
    real = (notify_new_listings.send_card, advisor.fetch_query)
    notify_new_listings.send_card = lambda card: sent.append(card)
    advisor.fetch_query = _fake_tcg_search
    url = "https://www.target.com/p/-/A-1"
    state = {"schema_version": 4, f"target::{url}": {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00", "in_stock": True, "new_announced": False, "signal": "page"}}
    with tempfile.TemporaryDirectory() as d:
        notify_new_listings.STATE_FILE, notify_new_listings.MARKET_FILE, notify_new_listings.CONFIG_FILE = Path(d) / "state.json", Path(d) / "market.json", Path(d) / "config.json"
        notify_new_listings.CONFIG_FILE.write_text(json.dumps({"map_url": "https://m"}))
        notify_new_listings.STATE_FILE.write_text(json.dumps(state))
        try:
            notify_new_listings.main()
            entry = json.loads(notify_new_listings.STATE_FILE.read_text())[f"target::{url}"]
        finally:
            notify_new_listings.send_card, advisor.fetch_query = real
    assert len(sent) == 1 and entry["armed"] is False and entry["last_stock_alert"] and entry["out_since"] is None, "the announced stay is disarmed so it is not announced again as a restock"


def test_new_listing_announced_once_and_only_when_in_stock():
    import tempfile
    sent = []
    real = (notify_new_listings.send_card, advisor.fetch_query)
    notify_new_listings.send_card = lambda card: sent.append(card)
    advisor.fetch_query = _fake_tcg_search
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00"}
    state = {
        "schema_version": 4,
        "target::https://www.target.com/p/-/A-1": {**base, "in_stock": True, "new_announced": False, "signal": "structured", "confirmed": True},
        "target::https://www.target.com/p/-/A-2": {**base, "in_stock": False, "new_announced": False},
        "target::https://www.target.com/p/-/A-3": {**base, "in_stock": None, "new_announced": False},
        "target::https://www.target.com/p/-/A-4": {**base, "in_stock": True},  # legacy entry: never new
        "target::https://www.target.com/p/-/A-5": {**base, "in_stock": True, "new_announced": False, "signal": "text", "price": 59.99, "msrp": 49.99},
    }
    with tempfile.TemporaryDirectory() as d:
        notify_new_listings.STATE_FILE = Path(d) / "state.json"
        notify_new_listings.MARKET_FILE = Path(d) / "market.json"
        notify_new_listings.CONFIG_FILE = Path(d) / "config.json"
        notify_new_listings.CONFIG_FILE.write_text(json.dumps({"map_url": "https://m"}))
        notify_new_listings.STATE_FILE.write_text(json.dumps(state))
        try:
            notify_new_listings.main()
            after = json.loads(notify_new_listings.STATE_FILE.read_text())
            notify_new_listings.main()  # a second run must not announce again
        finally:
            notify_new_listings.send_card, advisor.fetch_query = real
    assert [c["url"][-3:] for c in sent] == ["A-1", "A-5"], [c["url"] for c in sent]
    assert all(c["kind"] == "new" for c in sent)
    assert sent[0]["signal"] == "structured" and sent[1]["signal"] == "text" and sent[1]["price"] == 59.99
    assert sent[0]["confirmed"] is True and sent[1]["confirmed"] is False, "a new-listing card carries whether the in-stock reading was confirmed"
    assert notify.stock_embed(sent[0])["embeds"][0]["title"].startswith("🆕")
    assert all(v.get("new_announced", True) for k, v in after.items() if k != "schema_version"), "every decided listing is marked, in stock or not"


def test_a_new_best_buy_listing_alert_also_carries_the_add_to_cart_sku():
    import tempfile
    sent = []
    real = (notify_new_listings.send_card, advisor.fetch_query)
    notify_new_listings.send_card = lambda card: sent.append(card)
    advisor.fetch_query = _fake_tcg_search
    pb = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45"
    other = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-chaos-rising-elite-trainer-box/JJG2TL34RT"
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00", "in_stock": True, "new_announced": False, "signal": "browser"}
    state = {"schema_version": 4, f"bestbuy::{pb}": dict(base), f"bestbuy::{other}": {**base, "sku": "6600001"}}
    with tempfile.TemporaryDirectory() as d:
        notify_new_listings.STATE_FILE, notify_new_listings.MARKET_FILE, notify_new_listings.CONFIG_FILE = Path(d) / "state.json", Path(d) / "market.json", Path(d) / "config.json"
        notify_new_listings.CONFIG_FILE.write_text(json.dumps({"map_url": "https://m", "bestbuy_skus": {"JJG2TL8J45": "6678361"}}))
        notify_new_listings.STATE_FILE.write_text(json.dumps(state))
        try:
            notify_new_listings.main()
        finally:
            notify_new_listings.send_card, advisor.fetch_query = real
    got = {c["url"]: c["add_url"] for c in sent}
    assert got == {pb: "https://api.bestbuy.com/click/-/6678361/cart", other: "https://api.bestbuy.com/click/-/6600001/cart"}, got


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


def _fixture(name):
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def _guide():
    return json.loads((ROOT / "docs" / "30th_prices.json").read_text(encoding="utf-8"))


def test_every_guide_product_has_a_tcgplayer_mapping():
    ids = {p["id"] for p in _guide()["products"]}
    assert ids == set(prices_mod.PRODUCT_IDS), (ids ^ set(prices_mod.PRODUCT_IDS))
    sealed = prices_mod.sealed_prices(_fixture("tcg_sealed.json"))
    for guide_id, tcg_ids in prices_mod.PRODUCT_IDS.items():
        assert all(i in sealed for i in tcg_ids), f"{guide_id} maps to a TCGplayer id that is not in the captured response"


def test_each_mapping_points_at_the_product_it_claims():
    # Independent expectations, written out by hand from TCGplayer's product names (not derived from the code).
    expected = {
        "night-upc": "Ultra-Premium Collection [Night]", "day-upc": "Ultra-Premium Collection [Day]",
        "pc-etb": "Pokemon Center Elite Trainer Box", "etb": "30th Celebration Elite Trainer Box",
        "ditto": "Ditto Premium Collection", "booster-bundle": "30th Celebration Booster Bundle",
        "poster": "Poster Collection", "binder": "Binder Collection", "mew-figure": "Figure Collection [Mew]",
        "mewtwo-figure": "Figure Collection [Mewtwo]", "greninja-box": "Greninja ex Box", "sylveon-box": "Sylveon ex Box",
        "tech-lucario": "Tech Sticker Collection [Lucario]", "tech-exeggutor": "Tech Sticker Collection [Alolan Exeggutor]",
        "knockout": "Knock Out Collection", "blister": "2-Pack Blister", "battle-espeon": "Battle Deck [Espeon ex]",
        "battle-umbreon": "Battle Deck [Umbreon ex]", "tin-sylveon": "ex Tin [Sylveon ex] (Retail Version)",
        "tin-greninja": "ex Tin [Greninja ex] (Retail Version)",
    }
    names = {int(r["productId"]): r["productName"] for r in prices_mod.rows(_fixture("tcg_sealed.json"))}
    for guide_id, fragment in expected.items():
        (tcg_id,) = prices_mod.PRODUCT_IDS[guide_id]
        assert fragment in names[tcg_id], f"{guide_id} -> {names[tcg_id]!r}, expected it to contain {fragment!r}"
        assert names[tcg_id].count("Case") == 0, f"{guide_id} must not point at a sealed case"
    assert len(prices_mod.PRODUCT_IDS["mini-tins"]) == 10 and all("Mini Tin [" in names[i] and "Display" not in names[i] for i in prices_mod.PRODUCT_IDS["mini-tins"])


def test_bad_prices_and_unsorted_rows_are_handled():
    rows = [{"productId": 1, "productName": "a", "marketPrice": 0, "totalListings": 5},
            {"productId": 2, "productName": "b", "marketPrice": None, "totalListings": 5},
            {"productId": 3, "productName": "c", "marketPrice": 12.345, "totalListings": 7}]
    assert prices_mod.sealed_prices({"results": [{"results": rows}]}) == {3: {"market": 12.35, "low": None, "listings": 7}}, "zero and missing prices are not prices"
    cards = [{"productId": i, "productName": f"card{i}", "marketPrice": p, "rarityName": "R", "totalListings": 1} for i, p in enumerate([5, 90, 0, 40, 70])]
    ranked = prices_mod.chase_cards({"results": [{"results": cards}]}, limit=3)
    assert [c["market"] for c in ranked] == [90, 70, 40] and [c["rank"] for c in ranked] == [1, 2, 3], "sorted by price whatever order the API returns"


def test_prices_are_matched_by_product_id_and_stamped():
    data = _guide()
    for p in data["products"]:
        p["market"] = 1.0  # prove the update replaces stale values
        p.pop("market_updated_at", None)
    now = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    updated, flagged, missing = prices_mod.apply_prices(data, prices_mod.sealed_prices(_fixture("tcg_sealed.json")), now)
    # old values of 1.0 are far outside the jump guard, so nothing may update when the guard is working...
    assert flagged and not updated
    data = _guide()
    updated, flagged, missing = prices_mod.apply_prices(data, prices_mod.sealed_prices(_fixture("tcg_sealed.json")), now)
    assert len(updated) == 21 and not flagged and not missing
    by_id = {p["id"]: p for p in data["products"]}
    assert by_id["etb"]["market"] == 157.09 and by_id["etb"]["listings"] == 310
    assert by_id["pc-etb"]["market"] == 305.49, "the Pokémon Center ETB must not be confused with the regular ETB"
    assert by_id["battle-umbreon"]["market"] == 69.69
    assert by_id["mini-tins"]["market"] == 34.1, "mini tins use the median of the ten designs"
    assert all(p["market_updated_at"] == now.isoformat() and p["market_source"] == "tcgplayer" for p in data["products"])


def test_a_glitchy_jump_is_ignored_and_flagged():
    data = _guide()
    etb = next(p for p in data["products"] if p["id"] == "etb")
    etb["market"] = 20.0  # live price 157.09 is 7.8x this
    stamp = etb.get("market_updated_at")
    updated, flagged, _ = prices_mod.apply_prices(data, prices_mod.sealed_prices(_fixture("tcg_sealed.json")), datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert "etb" in flagged and "etb" not in updated
    assert etb["market"] == 20.0 and etb.get("market_updated_at") == stamp and "ignored" in etb["market_flag"]


def test_refresh_failure_keeps_prices_and_says_stale():
    data = _guide()
    before = json.dumps(data["products"], sort_keys=True)
    updated_at = data["updated_at"]

    def boom(body):
        raise RuntimeError("HTTP 500")

    out = prices_mod.refresh(data, fetch=boom, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert out["status"] == "stale" and "HTTP 500" in out["last_error"]
    assert json.dumps(out["products"], sort_keys=True) == before, "prior prices must survive a failed refresh"
    assert out["updated_at"] == updated_at, "updated_at may only move when prices really refreshed"
    assert out["checked_at"].startswith("2026-10-05")


def test_thin_response_is_stale_not_live():
    data = _guide()
    updated_at = data["updated_at"]
    hits = json.dumps(data["hits"], sort_keys=True)
    thin = {"results": [{"totalResults": 1, "results": _fixture("tcg_sealed.json")["results"][0]["results"][:3]}]}
    out = prices_mod.refresh(data, fetch=lambda body: thin, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert out["status"] == "stale" and "refreshed" in out["last_error"]
    assert out["updated_at"] == updated_at and json.dumps(out["hits"], sort_keys=True) == hits


def test_full_refresh_is_live_with_real_chase_cards():
    data = _guide()
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def fake(body):
        return _fixture("tcg_cards.json") if body["filters"]["term"]["productTypeName"] == ["Cards"] else _fixture("tcg_sealed.json")

    out = prices_mod.refresh(data, fetch=fake, now=now)
    assert out["status"] == "live" and out["updated_at"] == now.isoformat() and "last_error" not in out
    assert out["refresh"]["updated"] == 21
    assert [h["rank"] for h in out["hits"]] == list(range(1, len(out["hits"]) + 1))
    assert out["hits"][0]["name"] == "Mew - R/RGB" and out["hits"][0]["market"] == 6000.27
    assert all(a["market"] >= b["market"] for a, b in zip(out["hits"], out["hits"][1:])), "chase list is sorted by real price"


def test_paging_collects_every_row():
    calls = []

    def fake(body):
        offset = body["from"]
        calls.append((offset, body["size"]))
        page = [{"productId": i, "productName": f"p{i}", "marketPrice": 1.0} for i in range(offset, min(offset + body["size"], 120))]
        return {"results": [{"totalResults": 120, "results": page}]}

    payload = prices_mod.fetch_all(fake, "Sealed Products")
    assert calls == [(0, 50), (50, 50), (100, 50)] and len(prices_mod.rows(payload)) == 120
    assert max(size for _, size in calls) <= prices_mod.PAGE_SIZE


def test_committed_guide_carries_per_product_freshness():
    data = _guide()
    assert data["status"] in ("live", "stale") and "checked_at" in data
    assert all(p.get("market_source") == "tcgplayer" and p.get("market_updated_at") for p in data["products"]), "every price needs a source and a timestamp"


def _page(name):
    return (ROOT / "tests" / "fixtures" / "pages" / name).read_text(encoding="utf-8")


GS_30TH = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-30th-celebration-elite-trainer-box/20036324.html"
GS_PITCH = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html"


def test_real_pages_classify_correctly():
    # Captured from GitHub's runner on 2026-10-04. The 30th ETB page says InStock in JSON-LD but data-available="false".
    unavailable = _page("gamestop_30th_etb_unavailable.html")
    assert "schema.org/InStock" in unavailable and 'data-available="false"' in unavailable, "fixture must keep the trap"
    assert classify_response(200, GS_30TH, unavailable) == (False, "ok", None), "the page's own flag beats JSON-LD"
    assert classify_response(200, GS_PITCH, _page("gamestop_pitch_black_etb_available.html")) == (True, "ok", "page")
    assert classify_response(200, "https://www.target.com/p/-/A-1010892076", _page("target_30th_etb_placeholder.html")) == (None, "cart_disabled", None)
    assert classify_response(200, "https://www.walmart.com/blocked?url=x", _page("walmart_blocked.html"))[:2] == (None, "blocked")
    # Mixed flags (related products on the page) are ambiguous, so the structured data decides, not a coin flip.
    mixed = '<div data-available="true"></div><div data-available="false"></div>"availability":"https://schema.org/InStock"'
    assert classify_response(200, GS_PITCH, mixed) == (True, "ok", "structured"), "mixed flags must defer to the structured data (a flag-based answer here would be False)"
    mixed_out = mixed.replace("InStock", "OutOfStock")
    assert classify_response(200, GS_PITCH, mixed_out) == (False, "ok", None)


def test_price_and_msrp_rules():
    config = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    assert monitor.extract_price(_page("gamestop_30th_etb_unavailable.html")) == 99.99
    assert monitor.extract_price(_page("gamestop_pitch_black_etb_available.html")) == 84.99
    assert monitor.extract_price("<html>no price</html>") is None
    assert monitor.msrp_for(config, GS_30TH) == 49.99
    assert monitor.msrp_for(config, "https://www.target.com/p/-/pokemon-booster-bundle/A-1") is None, "no documented retail price, so none is claimed"
    assert monitor.msrp_for(config, "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html") is None, "other sets' retail prices are not guessed"
    assert monitor.msrp_for(config, "https://www.bestbuy.com/product/some-plush/1") is None, "unknown products get no price rule"


def _gs_config(url):
    cfg = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    cfg.update({"retailers": ["gamestop"], "keywords": [], "seed_urls": {"gamestop": [url]}})
    return cfg


def test_main_gamestop_trap_never_alerts_but_a_real_listing_does_at_any_price():
    import tempfile
    prior = lambda url: {"schema_version": 4, f"gamestop::{url}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    # 1. JSON-LD says InStock but the page says unavailable: no alert, and the state says not in stock.
    with tempfile.TemporaryDirectory() as d:
        _, sent = _run_main(Path(d), _gs_config(GS_30TH), prior(GS_30TH), {"gamestop.com": lambda u: _Resp(200, u, _page("gamestop_30th_etb_unavailable.html"))})
        entry = json.loads((Path(d) / "state.json").read_text())[f"gamestop::{GS_30TH}"]
    assert sent == [] and entry["in_stock"] is False
    # 2. Really available at $84.99 (retail $49.99): price never blocks an alert, it is recorded and shown.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _, sent = _run_main(tmp, _gs_config(GS_PITCH), prior(GS_PITCH), {"gamestop.com": lambda u: _Resp(200, u, _page("gamestop_pitch_black_etb_available.html"))})
        state = json.loads((tmp / "state.json").read_text())
        entry = state[f"gamestop::{GS_PITCH}"]
        assert len(sent) == 1 and sent[0]["signal"] == "page" and "Verified" in notify.stock_embed(sent[0])["embeds"][0]["fields"][3]["value"], sent
        assert entry["in_stock"] is True and entry["price"] == 84.99 and entry["msrp"] is None
        assert sent[0]["market"]["market"] == 75.58 and sent[0]["verdict"]["label"] == "ABOVE MARKET", "the alert carries the live TCGplayer comparison"
        assert "pitch-black-etb" in json.loads((tmp / "market.json").read_text()), "and the price is cached for next time"
        _, sent = _run_main(tmp, _gs_config(GS_PITCH), state, {"gamestop.com": lambda u: _Resp(200, u, _page("gamestop_pitch_black_etb_available.html"))})
        assert sent == [], "no repeat alert while it stays in stock"


def test_live_hits_show_every_in_stock_item_with_its_price():
    import tempfile
    now = datetime.now(timezone.utc).isoformat()
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": now, "in_stock": True, "in_stock_since": now, "signal": "page"}
    state = {
        "schema_version": 4,
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/a/1.html": {**base, "price": 49.99, "msrp": 49.99},
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/b/2.html": {**base, "price": 84.99, "msrp": 49.99},
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/c/3.html": {**base},  # legacy entry without a price
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "state.json").write_text(json.dumps(state))
        (tmp / "alerts.json").write_text("[]")
        (tmp / "config.json").write_text("{}")
        old = (refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE)
        refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "config.json"
        try:
            refresh_live_hits.main()
        finally:
            refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE = old
        live = json.loads((tmp / "alerts.json").read_text())
    urls = {a["url"].rsplit("/", 1)[1] for a in live}
    assert urls == {"1.html", "2.html", "3.html"}, "an above-retail listing is still in stock, so it is still a live hit"
    second = next(a for a in live if a["url"].endswith("2.html"))
    assert second["price"] == 84.99 and second["msrp"] == 49.99 and second["signal"] == "page"


PB_URL = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html"
CONFIG_FOR_ADVISOR = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))


def test_advisor_query_building():
    assert advisor.clean_title("Pokemon Trading Card Game: Pitch Black Elite Trainer Box | GameStop") == "pitch black elite trainer box"
    assert advisor.clean_title("Pokémon Trading Card Game: Mega Evolution Pitch Black Elite Trainer Box : Target") == "pitch black elite trainer box"
    assert advisor.clean_title("Pokémon Trading Card Game: 30th Celebration Elite Trainer Box : Target") == "30th celebration elite trainer box"
    key, query, include, exclude = advisor.build_query("whatever", PB_URL, CONFIG_FOR_ADVISOR)
    assert (key, query) == ("pitch-black-etb", "Pitch Black Elite Trainer Box") and include == ["pitch black", "elite trainer box"] and "case" in exclude
    key, query, include, exclude = advisor.build_query("Pokemon Trading Card Game: Phantasmal Flames Elite Trainer Box | GameStop", "https://www.gamestop.com/x/1.html", CONFIG_FOR_ADVISOR)
    assert key == "auto:phantasmal flames elite trainer box" and include == ["phantasmal", "flames", "elite", "trainer", "box"]
    assert {"case", "pokemon center"} <= set(exclude)
    key, query, include, exclude = advisor.build_query("Pokemon Center Elite Trainer Box | GameStop", "https://x/1", {})
    assert advisor.clean_title("Pokémon Center Elite Trainer Box | GameStop") == "pokemon center elite trainer box"
    assert "pokemon center" not in exclude and "exclusive" not in exclude and "case" in exclude, "asking for the Pokémon Center version must not exclude it"
    center = advisor.pick(_fixture("tcg_search_pitch_black.json")["results"][0]["results"], ["pitch", "black", "pokemon", "center", "elite", "trainer", "box"], exclude)
    assert center["productName"].startswith("Pitch Black Pokemon Center Elite Trainer Box") and center["marketPrice"] == 119.43


def test_real_retailer_titles_find_their_tcgplayer_product():
    # Titles exactly as the retailers print them (captured from state.json on 2026-10-04); expected values written by hand.
    cases = [
        ("Pokemon Trading Card Game Scarlet & Violet 10 Destined Rivals Elite Trainer Box - Walmart.com", "destined rivals elite trainer box", "tcg_search_destined.json", 117.42),
        ("Pokemon Trading Card Games Mega Evolution 5 Pitch Black Elite Trainer Box - Walmart.com", "pitch black elite trainer box", "tcg_search_pitch_black.json", 75.58),
        ("Pokemon TCG 30th Celebration Elite Trainer Box ETB - Walmart.com", "30th celebration elite trainer box", "tcg_search_30th_etb.json", 156.92),
        ("Pokemon Trading Card Games Scarlet & Violet Destined Rivals Elite Trainer Box - Walmart Business Supplies", "destined rivals elite trainer box", "tcg_search_destined.json", 117.42),
        ("Pokemon Trading Card Game: 30th Celebration Elite Trainer Box | GameStop", "30th celebration elite trainer box", "tcg_search_30th_etb.json", 156.92),
        ("Pokémon Trading Card Game: Mega Evolution Pitch Black Elite Trainer Box : Target", "pitch black elite trainer box", "tcg_search_pitch_black.json", 75.58),
    ]
    for title, expected_query, fixture, market in cases:
        assert advisor.clean_title(title) == expected_query, (title, advisor.clean_title(title))
        key, query, include, exclude = advisor.build_query(title, "https://x.example/p/1", {})
        found = advisor.lookup(query, include, exclude, fetch=lambda q, f=fixture: _fixture(f))
        assert found and found["market"] == market, (title, found)
    assert advisor.clean_title("Pokemon TCG 30th Celebration Elite Trainer Box ETB - Walmart.com") == "30th celebration elite trainer box", "a trailing ETB must not become a required extra word"
    assert advisor.clean_title("Pokémon TCG: Phantasmal Flames ETB") == "phantasmal flames elite trainer box", "ETB alone means Elite Trainer Box"


def test_fetch_query_retries_once():
    attempts = []

    class Resp:
        def __init__(self, ok):
            self.ok = ok
        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("503")
        def json(self):
            return {"results": [{"results": []}]}

    real = advisor.requests.post
    try:
        responses = iter([RuntimeError("connection reset"), Resp(True)])
        def post(*a, **k):
            attempts.append(1)
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r
        advisor.requests.post = post
        assert advisor.fetch_query("x") == {"results": [{"results": []}]} and len(attempts) == 2, "one transient failure is retried"
        attempts.clear()
        def always(*a, **k):
            attempts.append(1)
            raise RuntimeError("down")
        advisor.requests.post = always
        try:
            advisor.fetch_query("x")
            raise AssertionError("expected the failure to surface after the retry")
        except RuntimeError:
            assert len(attempts) == 2, "exactly two attempts, then the error surfaces (market_for turns it into 'no price')"
    finally:
        advisor.requests.post = real


def test_advisor_picks_the_right_tcgplayer_product():
    rows = _fixture("tcg_search_pitch_black.json")["results"][0]["results"]
    row = advisor.pick(rows, ["pitch black", "elite trainer box"], ["case", "pokemon center", "exclusive", "display"])
    assert row["productName"] == "Pitch Black Elite Trainer Box" and row["marketPrice"] == 75.58, "not the case ($714), not the Pokémon Center box ($119)"
    assert advisor.pick(rows, ["pitch black", "elite trainer box"], ["pitch black"]) is None
    assert advisor.pick(rows, [], []) is None, "with nothing to match on, never guess a product"
    assert advisor.pick([{"productName": "Pitch Black Elite Trainer Box", "marketPrice": 0}], ["pitch black"], []) is None, "no price, no match"
    found = advisor.lookup("Pitch Black Elite Trainer Box", ["pitch black", "elite trainer box"], ["case", "pokemon center"], fetch=lambda q: _fixture("tcg_search_pitch_black.json"))
    assert found["market"] == 75.58 and found["product_id"] == 692947 and found["listings"] == 213


def test_advisor_cache_and_failure_behaviour():
    now = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    calls = []

    def fetch(query):
        calls.append(query)
        return _fixture("tcg_search_pitch_black.json")

    cache = {}
    first = advisor.market_for(CONFIG_FOR_ADVISOR, cache, "t", PB_URL, fetch=fetch, now=now)
    assert first["market"] == 75.58 and len(calls) == 1 and "pitch-black-etb" in cache
    advisor.market_for(CONFIG_FOR_ADVISOR, cache, "t", PB_URL, fetch=fetch, now=now + timedelta(minutes=9))
    assert len(calls) == 1, "a cache entry under 10 minutes old needs no network call"
    advisor.market_for(CONFIG_FOR_ADVISOR, cache, "t", PB_URL, fetch=fetch, now=now + timedelta(minutes=11))
    assert len(calls) == 2, "an entry older than 10 minutes is refreshed: an alert shows the price as it is now"

    def boom(query):
        raise RuntimeError("down")

    stale = advisor.market_for(CONFIG_FOR_ADVISOR, cache, "t", PB_URL, fetch=boom, now=now + timedelta(hours=20))
    assert stale["market"] == 75.58, "a failed refresh falls back to the stale price (its own timestamp shows the age)"
    assert advisor.market_for(CONFIG_FOR_ADVISOR, {}, "t", PB_URL, fetch=boom, now=now) is None, "no cache and no network: no market, no crash"
    nothing = {"results": [{"totalResults": 0, "results": []}]}
    assert advisor.market_for(CONFIG_FOR_ADVISOR, {}, "t", PB_URL, fetch=lambda q: nothing, now=now) is None
    cache = {}
    assert advisor.refresh_cache(CONFIG_FOR_ADVISOR, cache, [("t", PB_URL), ("x", "https://x/unknown")], fetch=fetch, now=now) == 1


def test_price_verdicts():
    v = advisor.verdict
    low = v(59.99, 49.99, 75.58)
    assert low["label"] == "BUY: LOW" and low["tone"] == "good" and "21% below" in low["summary"] and low["retail"] == "above MSRP (+20%)"
    assert v(84.99, 49.99, 75.58)["label"] == "ABOVE MARKET" and "12% above" in v(84.99, 49.99, 75.58)["summary"]
    assert v(75.00, 49.99, 75.58)["label"] == "FAIR PRICE" and v(75.00, 49.99, 75.58)["tone"] == "fair"
    assert v(67.0, None, 100.0)["label"] == "BUY: LOW", "10% or more under market is a deal"
    assert v(89.99, None, 100.0)["label"] == "BUY: LOW" and v(90.0, None, 100.0)["label"] == "BUY: LOW" and v(90.01, None, 100.0)["label"] == "FAIR PRICE"
    assert v(110.0, None, 100.0)["label"] == "FAIR PRICE" and v(110.01, None, 100.0)["label"] == "ABOVE MARKET"
    assert v(49.99, 49.99, None)["label"] == "AT RETAIL" and v(49.99, 49.99, None)["retail"] == "at retail"
    assert v(84.99, 49.99, None)["label"] == "ABOVE MSRP" and v(84.99, 49.99, None)["retail"] == "above MSRP (+70%)"
    assert v(None, 49.99, 75.0)["label"] == "PRICE UNKNOWN" and v(12.0, None, None)["label"] == "PRICE UNKNOWN"


def _card(price=84.99, **over):
    market = {"market": 75.58, "updated_at": "2026-10-04T15:56:00+00:00", "product_id": 692947}
    card = advisor.build_card("gamestop", "stock", "Pokemon TCG: Pitch Black Elite Trainer Box", PB_URL, "https://poncema4.github.io/pokemon-alert-bot/", "2026-10-04T16:00:00+00:00", "page", price, 49.99, market, ping=True, now=datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc))
    card.update(over)
    return card


def test_stock_embed_is_clean_linked_and_within_discord_limits():
    card = _card()
    assert card["retailer"] == "GameStop" and card["market_age"] == "updated 4 min ago" and card["market_url"].endswith("/692947")
    payload = notify.stock_embed(card)
    embed = payload["embeds"][0]
    assert embed["title"] == "🟢 IN STOCK · GameStop"
    assert embed["description"].startswith("**[Pokemon TCG: Pitch Black Elite Trainer Box](" + PB_URL + ")**"), "the product name is the hyperlink"
    assert "**ABOVE MARKET**" in embed["description"] and "12% above" in embed["description"]
    assert payload["content"] == "@everyone" and payload["allowed_mentions"] == {"parse": ["everyone"]}
    names = [f["name"] for f in embed["fields"]]
    assert names == ["Price", "Retail", "TCGplayer market", "Proof", "Links"]
    values = {f["name"]: f["value"] for f in embed["fields"]}
    assert values["Price"] == "$84.99" and values["Retail"].startswith("$49.99") and "above MSRP (+70%)" in values["Retail"] and values["TCGplayer market"].startswith("$75.58")
    assert "[Product page](" + PB_URL + ")" in values["Links"] and "[Map](" in values["Links"] and "tcgplayer.com/product/692947" in values["Links"]
    # No raw URL anywhere: every http(s) address sits inside a markdown link, so Discord never unfurls a giant preview.
    texts = [embed["title"], embed["description"]] + list(values.values())
    for text in texts:
        for m in re.finditer(r"https?://", text):
            assert text[max(0, m.start() - 2):m.start()] == "](", f"raw link in {text!r}"
    assert "image" not in embed and "thumbnail" not in embed and "video" not in embed


def test_embed_ping_rules_and_tones():
    assert notify.stock_embed(_card(ping=False))["content"] == "" and notify.stock_embed(_card(ping=False))["allowed_mentions"] == {"parse": []}
    test_payload = notify.stock_embed(_card(kind="test", ping=True))
    assert test_payload["content"] != "@everyone" and test_payload["allowed_mentions"] == {"parse": []} and test_payload["embeds"][0]["title"].startswith("🧪 TEST")
    assert notify.stock_embed(_card(kind="new"))["embeds"][0]["title"].startswith("🆕")
    assert notify.stock_embed(_card(price=59.99))["embeds"][0]["color"] == notify.COLORS["good"]
    assert notify.stock_embed(_card(price=75.0))["embeds"][0]["color"] == notify.COLORS["fair"]
    assert notify.stock_embed(_card())["embeds"][0]["color"] == notify.COLORS["high"]
    assert notify.stock_embed(_card(market=None, market_age="", market_url="", price=None, msrp=None, verdict=advisor.verdict(None, None, None)))["embeds"][0]["fields"][2]["value"] == "not found"
    for signal, word in (("page", "Verified"), ("structured", "Verified"), ("text", "Likely"), ("browser", "Verified")):
        assert word in {f["name"]: f["value"] for f in notify.stock_embed(_card(signal=signal))["embeds"][0]["fields"]}["Proof"]


def test_embed_survives_hostile_lengths():
    huge = "X" * 5000
    payload = notify.stock_embed(_card(title=huge))
    embed = payload["embeds"][0]
    assert len(embed["title"]) <= 256 and len(embed["description"]) <= 4096 and all(len(f["value"]) <= 1024 and len(f["name"]) <= 256 for f in embed["fields"])
    assert len(embed["fields"]) <= 25 and notify.embed_size(payload) <= 6000, notify.embed_size(payload)
    assert notify.clip("abc", 10) == "abc" and notify.clip("abcdef", 4) == "abc…" and notify.clip(None, 5) == ""
    note = notify.notice_embed("blind", "⚠️ BLIND SPOT · Walmart", "Walmart cannot be read.", ping=True)
    assert note["content"] == "@everyone" and note["embeds"][0]["color"] == notify.COLORS["blind"]


def test_post_sends_payload_and_never_raises():
    sent = []

    class Resp:
        status_code = 200
        content = b"{}"
        def json(self):
            return {"id": "123"}

    real_url, real_post = notify.DISCORD_WEBHOOK_URL, notify.requests.post
    try:
        notify.DISCORD_WEBHOOK_URL = ""
        notify.requests.post = lambda *a, **k: sent.append((a, k)) or Resp()
        assert notify.post({"content": "x"}) == (None, None) and sent == [], "no webhook configured: nothing is sent"
        notify.DISCORD_WEBHOOK_URL = "https://discord.test/api/webhooks/1/abc"
        assert notify.post({"content": "x"}, wait=True) == (200, {"id": "123"})
        assert sent[0][0][0].endswith("?wait=true") and sent[0][1]["json"] == {"content": "x"}

        def boom(*a, **k):
            raise RuntimeError("network down")

        notify.requests.post = boom
        assert notify.post({"content": "x"}) == (None, None), "a Discord outage must not crash the monitor"
    finally:
        notify.DISCORD_WEBHOOK_URL, notify.requests.post = real_url, real_post


def test_one_listing_is_one_url():
    c = monitor.canonical_url
    base = "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ"
    assert c(base + "#tabbed-customerreviews") == base and c(base + "?skuId=13089535&utm_source=x") == base and c(base + "/") == base
    assert c("HTTPS://WWW.Target.com/p/-/A-1010892076?preselect=1") == "https://www.target.com/p/-/A-1010892076"
    assert c("https://www.walmart.com/ip/Some-Name/123?athbdg=L1600") == "https://www.walmart.com/ip/Some-Name/123"
    assert c("not a url") == "not a url" and c("") == ""
    # Discovery from a search page: the same product linked three ways is one listing.
    page = f'<a href="{base}">a</a><a href="{base}#tabbed-customerreviews">b</a><a href="{base}?x=1">c</a><a href="{base}/#overview">d</a>'
    assert monitor.extract_retailer_urls("bestbuy", page) == [base]


def test_seed_urls_with_tracking_junk_are_requested_and_stored_canonically():
    import tempfile
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": ["https://www.target.com/p/-/A-1?preselect=1#reviews", "https://www.target.com/p/-/A-1"]}}
    sold = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, {"schema_version": 4}, {"target.com": sold})
        keys = [k for k in json.loads((Path(d) / "state.json").read_text()) if k != "schema_version"]
    assert session.calls == ["https://www.target.com/p/-/A-1"], "two spellings of one seed are fetched once"
    assert keys == ["target::https://www.target.com/p/-/A-1"]


def test_state_merges_url_variants_and_keeps_the_newest_reading():
    base = "https://www.bestbuy.com/product/pokemon-30th-celebration-elite-trainer-box/JJG2TL8XCJ"
    entry = lambda ok, stock: {"pokemon": True, "title": "Pokemon 30th Celebration Elite Trainer Box", "in_stock": stock, "last_ok": ok, "last_seen": ok}
    state = {"schema_version": 4,
             f"bestbuy::{base}#tabbed-customerreviews": entry("2026-10-04T10:00:00+00:00", False),
             f"bestbuy::{base}": entry("2026-10-04T12:00:00+00:00", True),
             f"bestbuy::{base}?skuId=1": entry("2026-10-04T11:00:00+00:00", False)}
    cleaned = monitor.clean_state(state)
    assert list(cleaned) == ["schema_version", f"bestbuy::{base}"], list(cleaned)
    assert cleaned[f"bestbuy::{base}"]["in_stock"] is True and cleaned[f"bestbuy::{base}"]["last_ok"].startswith("2026-10-04T12"), "newest reading wins"
    assert monitor.clean_state({"schema_version": 4, "target::https://www.target.com/p/-/A-1?x=1": {"pokemon": True, "title": "Pokemon"}}).get("target::https://www.target.com/p/-/A-1")


def test_new_listing_burst_is_capped_with_one_summary():
    import tempfile
    sent, notices = [], []
    real = (notify_new_listings.send_card, notify_new_listings.alert, advisor.fetch_query)
    notify_new_listings.send_card = lambda card: sent.append(card)
    notify_new_listings.alert = lambda title, body, url="", ping=False, tone="blind": notices.append((title, body))
    advisor.fetch_query = _fake_tcg_search
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00", "in_stock": True, "new_announced": False, "signal": "page"}
    state = {"schema_version": 4, **{f"bestbuy::https://www.bestbuy.com/product/x/J{n}": dict(base) for n in range(12)}}
    with tempfile.TemporaryDirectory() as d:
        notify_new_listings.STATE_FILE = Path(d) / "state.json"
        notify_new_listings.MARKET_FILE = Path(d) / "market.json"
        notify_new_listings.CONFIG_FILE = Path(d) / "config.json"
        notify_new_listings.CONFIG_FILE.write_text("{}")
        notify_new_listings.STATE_FILE.write_text(json.dumps(state))
        try:
            notify_new_listings.main()
            after = json.loads(notify_new_listings.STATE_FILE.read_text())
            notify_new_listings.main()
        finally:
            notify_new_listings.send_card, notify_new_listings.alert, advisor.fetch_query = real
    assert len(sent) == notify_new_listings.MAX_NEW_PER_RUN == 5, "only the first five get a card"
    assert len(notices) == 1 and "7 more" in notices[0][1], notices
    assert all(v.get("new_announced") for k, v in after.items() if k != "schema_version"), "all twelve are decided, so the rest never alert later"
    assert len(sent) == 5 and len(notices) == 1, "a second run adds nothing"


def _run_live_hits(tmp, state, alerts, now):
    (tmp / "state.json").write_text(json.dumps(state))
    (tmp / "alerts.json").write_text(json.dumps(alerts))
    (tmp / "config.json").write_text(json.dumps({"alert_ttl_minutes": 15}))
    old = (refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE)
    refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "config.json"
    try:
        refresh_live_hits.main(now=now)
    finally:
        refresh_live_hits.STATE_FILE, refresh_live_hits.ALERTS_FILE, refresh_live_hits.CONFIG_FILE = old
    return json.loads((tmp / "alerts.json").read_text())


def test_a_live_hit_clears_fifteen_minutes_after_it_came_into_stock():
    import tempfile
    t0 = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    url = "https://www.gamestop.com/toys-games/trading-cards/products/a/1.html"
    key = f"gamestop::{url}"
    entry = lambda since, stock=True, seen=t0 + timedelta(minutes=0): {"pokemon": True, "title": "Pokemon ETB", "in_stock": stock, "in_stock_since": since, "last_seen": seen.isoformat(), "signal": "page"}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 5 minutes in: live, and the expiry is fixed at in-stock time + 15 minutes.
        now = t0 + timedelta(minutes=5)
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry(t0.isoformat(), seen=now)}, [], now)
        assert len(live) == 1 and live[0]["expires_at"] == (t0 + timedelta(minutes=15)).isoformat() and live[0]["detected_at"] == t0.isoformat()
        # 14 minutes in: still live, expiry NOT extended by the later check.
        now = t0 + timedelta(minutes=14)
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry(t0.isoformat(), seen=now)}, live, now)
        assert len(live) == 1 and live[0]["expires_at"] == (t0 + timedelta(minutes=15)).isoformat(), "checking again must not extend the 15 minutes"
        # 16 minutes in: still in stock, but the hit clears.
        now = t0 + timedelta(minutes=16)
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry(t0.isoformat(), seen=now)}, live, now)
        assert live == [], "after 15 minutes the live section must not show it, even though it is still in stock"
        # Going out of stock clears it at once, even inside the window.
        now = t0 + timedelta(minutes=3)
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry(t0.isoformat(), seen=now)}, [], now)
        assert len(live) == 1
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry(None, stock=False, seen=now)}, live, now)
        assert live == [], "out of stock is not live"
        # Back in stock later (a new stay) is a fresh hit with a fresh 15 minutes.
        later = t0 + timedelta(minutes=40)
        live = _run_live_hits(tmp, {"schema_version": 4, key: entry((t0 + timedelta(minutes=38)).isoformat(), seen=later)}, [], later)
        assert len(live) == 1 and live[0]["expires_at"] == (t0 + timedelta(minutes=53)).isoformat()
        # An entry with no known start (older than this feature) is never shown as live; neither is one not checked for 5+ minutes.
        assert _run_live_hits(tmp, {"schema_version": 4, key: {**entry(None), "in_stock_since": None}}, [], t0) == []
        assert _run_live_hits(tmp, {"schema_version": 4, key: entry(t0.isoformat(), seen=t0)}, [], t0 + timedelta(minutes=6)) == [], "stale checks are not live"


def test_live_online_never_shows_an_item_that_is_not_confirmed_in_stock_even_with_a_leftover_start_time():
    """Defence in depth: a sold-out or unknown entry that still carries an in_stock_since (a corrupt or older state file) must not be shown."""
    import tempfile
    t0 = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for stock in (False, None):
            key = "target::https://www.target.com/p/-/A-1"
            state = {"schema_version": 4, key: {"pokemon": True, "title": "Pokemon ETB", "in_stock": stock, "in_stock_since": t0.isoformat(), "last_seen": t0.isoformat(), "price": 59.99}}
            assert _run_live_hits(tmp, state, [], t0 + timedelta(minutes=1)) == [], f"in_stock={stock} with a recent start time is still not a live hit"


def test_monitor_tracks_when_a_stay_in_stock_began():
    import tempfile
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": ["https://www.target.com/p/-/A-1"]}}
    url_key = "target::https://www.target.com/p/-/A-1"
    in_stock = lambda u: _Resp(200, u, '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"')
    sold_out = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-03T00:00:00+00:00"}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # transition from not in stock: starts now
        _run_main(tmp, config, {"schema_version": 4, url_key: {**base, "in_stock": False}}, {"target.com": in_stock})
        started = json.loads((tmp / "state.json").read_text())[url_key]["in_stock_since"]
        assert started and started > "2026-10-04"
        # stays in stock: unchanged
        _run_main(tmp, config, json.loads((tmp / "state.json").read_text()), {"target.com": in_stock})
        assert json.loads((tmp / "state.json").read_text())[url_key]["in_stock_since"] == started
        # goes out of stock: cleared
        _run_main(tmp, config, json.loads((tmp / "state.json").read_text()), {"target.com": sold_out})
        assert json.loads((tmp / "state.json").read_text())[url_key]["in_stock_since"] is None
        # legacy in-stock entry with no in_stock_since: falls back to when it was first seen (old), never "just now"
        legacy = {"schema_version": 4, url_key: {**base, "in_stock": True, "first_seen": "2026-09-01T00:00:00+00:00"}}
        _run_main(tmp, config, legacy, {"target.com": in_stock})
        assert json.loads((tmp / "state.json").read_text())[url_key]["in_stock_since"] == "2026-09-01T00:00:00+00:00"


WATCH = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))["watchlist"]


def _watch(item_id):
    return next(w for w in WATCH if w["id"] == item_id)


def test_the_watchlist_covers_the_etbs_marco_named():
    labels = {w["label"] for w in WATCH}
    for named in ("30th Celebration", "Delta Reign", "Pitch Black", "Chaos Rising", "Prismatic Evolutions", "Mega Evolution", "Phantasmal Flames", "Ascended Heroes", "Perfect Order", "Destined Rivals"):
        assert named in labels, f"{named} is missing from the watchlist"
    assert len({w["id"] for w in WATCH}) == len(WATCH)
    assert {w["id"]: w["msrp"] for w in WATCH if w["msrp"]} == {"30th-celebration": 49.99, "30th-upc-day": 179.99, "30th-upc-night": 179.99}, "a retail price is only stated where it is documented; never a guess"
    assert {"30th-upc-day", "30th-upc-night"} <= {w["id"] for w in WATCH}, "the 30th Ultra-Premium Collections are watched"
    keywords = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))["keywords"]
    for w in WATCH:
        assert w.get("keyword", f"pokemon {w['label'].lower()} elite trainer box") in keywords, f"discovery does not search for {w['label']}"


def test_every_watched_etb_a_store_can_be_read_for_has_a_seed_url():
    cfg = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    gamestop = cfg["seed_urls"]["gamestop"]
    assert len(gamestop) == len(set(gamestop)), "no duplicate seeds"
    for w in WATCH:
        if w["id"] in ("151", "black-bolt", "journey-together"):
            continue  # GameStop has no 151 ETB, and removed the Black Bolt and Journey Together pages (HTTP 410)
        mine = [u for u in gamestop if coverage.matches(w, "", u)]
        assert mine, f"no GameStop seed for the {w['label']} Elite Trainer Box"
        assert all(monitor.retailer_url_is_valid("gamestop", u) for u in mine)
    assert not [u for u in gamestop if coverage.matches(_watch("mega-evolution"), "", u) and "mega-evolution-elite-trainer-box" not in u], "the base-set seed must be the base set only"


def test_ultra_premium_collections_are_watched_and_priced():
    assert coverage.matches(_watch("30th-upc-day"), "Pokemon Trading Card Game: 30th Celebration Ultra-Premium Collection (Styles May Vary)", GS + "pokemon-trading-card-game-30th-celebration-ultra-premium-collection-styles-may-vary/450768.html")
    assert not coverage.matches(_watch("30th-upc-night"), "Pokemon Trading Card Game: 30th Celebration Elite Trainer Box", GS + "x/1.html"), "an ETB is not a UPC"
    assert not coverage.matches(_watch("30th-celebration"), "Pokemon Trading Card Game: 30th Celebration Ultra-Premium Collection", GS + "x/450768.html"), "a UPC is not the ETB"
    state = {"schema_version": 4, "gamestop::" + GS + "pokemon-trading-card-game-30th-celebration-ultra-premium-collection-styles-may-vary/450768.html": _entry("Pokemon Trading Card Game: 30th Celebration Ultra-Premium Collection (Styles May Vary)", True, 399.99, since="2026-11-06T14:00:00+00:00")}
    rows = {r["id"]: r for r in coverage.build_coverage(state, WATCH, {})["rows"]}
    assert rows["30th-upc-day"]["retailers"]["gamestop"]["state"] == "in_stock" and rows["30th-upc-night"]["retailers"]["gamestop"]["price"] == 399.99
    assert rows["30th-upc-day"]["retailers"]["pokemoncenter"]["state"] == "unreadable", "Pokémon Center, where the UPC is sold at retail, cannot be read"
    # Real TCGplayer names (captured 2026-10-04): the Night box first, then a case, then the Day box, so only the right pick passes.
    rows = [{"productId": 704191, "productName": "30th Celebration Ultra-Premium Collection [Night]", "marketPrice": 722.03, "totalListings": 15},
            {"productId": 709029, "productName": "30th Celebration Ultra-Premium Collection Case", "marketPrice": 2341.93, "totalListings": 8},
            {"productId": 704190, "productName": "30th Celebration Ultra-Premium Collection [Day]", "marketPrice": 565.59, "totalListings": 21}]
    fetch = lambda q: {"results": [{"totalResults": 3, "results": rows}]}
    cache = {}
    advisor.refresh_watchlist({"watchlist": [_watch("30th-upc-day"), _watch("30th-upc-night")]}, cache, fetch=fetch)
    assert cache["watch:30th-upc-day"]["market"] == 565.59 and cache["watch:30th-upc-day"]["product_id"] == 704190, "Day is the Day box, not Night and not the case"
    assert cache["watch:30th-upc-night"]["market"] == 722.03 and cache["watch:30th-upc-night"]["product_id"] == 704191
    assert advisor.pick(rows, ["ultra premium collection", "day"], []) ["productId"] == 704190 and advisor.pick(rows, ["ultra-premium collection"], ["case"])["productId"] == 704191, "hyphen and space spellings are the same product"


def test_walmart_alerts_carry_a_real_add_to_cart_link_and_the_links_are_plainly_named():
    walmart = advisor.build_card("walmart", "stock", "Pokemon ETB", "https://www.walmart.com/ip/Pokemon-TCG-Elite-Trainer-Box/15718673510?athbdg=L1600", "https://m", "2026-10-04T16:00:00+00:00", "page", 59.99, None, None)
    assert walmart["add_url"] == "https://affil.walmart.com/cart/addToCart?items=15718673510"
    links = {f["name"]: f["value"] for f in notify.stock_embed(walmart)["embeds"][0]["fields"]}["Links"]
    assert "[Add to cart](https://affil.walmart.com/cart/addToCart?items=15718673510)" in links
    assert links.index("[Product page]") < links.index("[Add to cart]") < links.index("[Map]"), links
    assert "[My cart]" not in links and "cart_url" not in walmart, "the plain cart page link was useless and is gone"
    assert advisor.add_to_cart_url("walmart", "https://www.walmart.com/ip/15718673510") == "https://affil.walmart.com/cart/addToCart?items=15718673510", "id-only product URL"
    for retailer in ("target", "bestbuy", "gamestop", "pokemoncenter", "walmart"):  # no SKU / id given here
        other = advisor.build_card(retailer, "stock", "Pokemon ETB", "https://example.test/p/1", "https://m", "2026-10-04T16:00:00+00:00", "page", None, None, None)
        assert other["add_url"] == "" and "[Add to cart]" not in {f["name"]: f["value"] for f in notify.stock_embed(other)["embeds"][0]["fields"]}["Links"], retailer + " has no public add-to-cart link, so none is shown"
    assert "Open product" not in (ROOT / "bot" / "notify.py").read_text(encoding="utf-8"), "the confusing old labels are gone"


def test_best_buy_alerts_add_to_cart_with_the_numeric_sku():
    cfg = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
    pb = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45"
    thirtieth = "https://www.bestbuy.com/product/pokemon-trading-card-game-30th-celebration-elite-trainer-box/JJG2TL8XCJ"
    # SKUs verified against Best Buy's own /sku/ pages and model numbers on 2026-10-04
    assert advisor.sku_for(cfg, pb) == "6678361" and advisor.sku_for(cfg, thirtieth) == "6685559"
    assert advisor.sku_for(cfg, pb, "6678361") == "6678361", "a SKU read from the page is used"
    assert advisor.sku_for(cfg, "https://www.bestbuy.com/product/x/JJG2TL0000") is None, "an unknown product has no SKU, so no add link"
    assert advisor.sku_for(cfg, pb, "JJG2TL8J45") == "6678361", "a product code is not a SKU: Best Buy answers Invalid SKU, so the verified one is used"
    assert advisor.sku_for({}, pb, "abc") is None
    card = advisor.build_card("bestbuy", "stock", "Pokemon Pitch Black ETB", pb, "https://m", "2026-10-04T16:00:00+00:00", "browser", 49.99, 49.99, None, sku="6678361")
    assert card["add_url"] == "https://api.bestbuy.com/click/-/6678361/cart"
    links = {f["name"]: f["value"] for f in notify.stock_embed(card)["embeds"][0]["fields"]}["Links"]
    assert "[Add to cart](https://api.bestbuy.com/click/-/6678361/cart)" in links and "[Product page](" in links and "[My cart]" not in links
    assert [x.split("](")[0] for x in links.split("  ·  ")] == ["[Product page", "[Add to cart", "[Map"], links   # exactly these, no TCGplayer without a market price
    assert advisor.add_to_cart_url("bestbuy", pb, None) == "" and advisor.add_to_cart_url("bestbuy", pb, "JJG2TL8J45") == "", "never link a code Best Buy rejects"


def test_watchlist_matching_is_by_whole_word_and_regular_boxes_only():
    m = coverage.matches
    assert m(_watch("pitch-black"), "Pokemon Trading Card Game: Pitch Black Elite Trainer Box | GameStop", "https://www.gamestop.com/x/445744.html")
    assert m(_watch("30th-celebration"), "Pokemon TCG 30th Celebration Elite Trainer Box ETB - Walmart.com", "https://www.walmart.com/ip/20754418655")
    assert m(_watch("delta-reign"), "Pokémon TCG: Mega Evolution Delta Reign ETB", "https://www.target.com/p/-/A-1")
    assert not m(_watch("pitch-black"), "Pitch Black Pokemon Center Elite Trainer Box", "https://x/1"), "the Pokémon Center box is a different product"
    assert not m(_watch("pitch-black"), "Pitch Black Elite Trainer Box Case", "https://x/1") and not m(_watch("pitch-black"), "Pitch Black Booster Bundle", "https://x/1"), "cases and other products are not the ETB"
    assert m(_watch("mega-evolution"), "Mega Evolution Elite Trainer Box [Mega Lucario]", "https://x/1")
    assert not m(_watch("mega-evolution"), "Mega Evolution Pitch Black Elite Trainer Box", "https://x/1"), "the base set must not swallow the later sets"
    assert m(_watch("151"), "Pokemon Scarlet & Violet 151 Elite Trainer Box", "https://x/1")
    assert not m(_watch("151"), "Pokemon Elite Trainer Box", "https://www.target.com/p/-/A-1011514"), "151 inside a product id is not the 151 set"
    assert not m(_watch("pitch-black"), "Pitch Black Elite Trainer Box Exclusive", "https://x/1")


def _entry(title, in_stock, price=None, reason=None, since=None):
    return {"pokemon": True, "title": title, "in_stock": in_stock, "price": price, "reason": reason, "in_stock_since": since}


def test_coverage_cells_never_confuse_unreadable_with_out_of_stock():
    state = {
        "schema_version": 4,
        "gamestop::https://www.gamestop.com/x/pb.html": _entry("Pitch Black Elite Trainer Box | GameStop", True, 84.99, since="2026-10-04T16:00:00+00:00"),
        "gamestop::https://www.gamestop.com/x/30.html": _entry("30th Celebration Elite Trainer Box | GameStop", False, 99.99),
        "target::https://www.target.com/p/-/A-1": _entry("Pitch Black Elite Trainer Box : Target", None, reason="cart_disabled"),
        "walmart::https://www.walmart.com/ip/1": _entry("Pitch Black Elite Trainer Box - Walmart.com", False, 78.99),
        "walmart::https://www.walmart.com/ip/2": _entry("Pitch Black Elite Trainer Box - Walmart Business Supplies", True, 114.99),
    }
    market = {"watch:pitch-black": {"market": 75.58, "updated_at": "2026-10-04T16:00:00+00:00", "product_id": 692947}}
    out = coverage.build_coverage(state, [_watch("pitch-black"), _watch("30th-celebration"), _watch("delta-reign")], market, now=datetime(2026, 10, 4, 16, 5, tzinfo=timezone.utc))
    rows = {r["id"]: r for r in out["rows"]}
    pb = rows["pitch-black"]
    assert pb["market"] == 75.58 and pb["market_url"].endswith("/692947") and pb["msrp"] is None
    assert pb["retailers"]["gamestop"] == {"state": "in_stock", "price": 84.99, "url": "https://www.gamestop.com/x/pb.html", "since": "2026-10-04T16:00:00+00:00"}
    assert pb["retailers"]["target"]["state"] == "unreadable" and pb["retailers"]["target"]["reason"] == "cart_disabled"
    assert pb["retailers"]["walmart"]["state"] == "in_stock" and pb["retailers"]["walmart"]["price"] == 114.99, "one in-stock listing wins over an out-of-stock one"
    assert pb["retailers"]["bestbuy"]["state"] == "not_tracked"
    assert pb["retailers"]["pokemoncenter"]["state"] == "unreadable", "Pokémon Center cannot be read, so it is never shown as out of stock or not tracked"
    cheaper = dict(state, **{"walmart::https://www.walmart.com/ip/3": _entry("Pitch Black Elite Trainer Box - Walmart.com", True, 79.99), "walmart::https://www.walmart.com/ip/4": _entry("Pitch Black Elite Trainer Box - Walmart.com", True, 140.00)})
    wm = {r["id"]: r for r in coverage.build_coverage(cheaper, [_watch("pitch-black")], market)["rows"]}["pitch-black"]["retailers"]["walmart"]
    assert wm["state"] == "in_stock" and wm["price"] == 79.99 and wm["url"].endswith("/ip/3"), "with several in-stock listings the lowest price is shown"
    assert rows["30th-celebration"]["msrp"] == 49.99
    assert rows["30th-celebration"]["retailers"]["gamestop"]["state"] == "out" and rows["30th-celebration"]["retailers"]["gamestop"]["price"] == 99.99
    assert rows["delta-reign"]["retailers"]["gamestop"]["state"] == "not_tracked" and rows["delta-reign"]["market"] is None
    # A page the store removed (404/410) is "gone", not "can't read"; a blocked page stays "unreadable".
    gone = coverage.build_coverage({"schema_version": 4, "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-black-bolt-elite-trainer-box/426102.html": _entry("Black Bolt Elite Trainer Box", None, reason="http_410"),
                                    "target::https://www.target.com/p/-/pokemon-black-bolt-elite-trainer-box/A-9": _entry("Black Bolt Elite Trainer Box", None, reason="blocked")}, WATCH, {})
    bb = {r["id"]: r for r in gone["rows"]}["black-bolt"]["retailers"]
    assert bb["gamestop"]["state"] == "gone" and bb["target"]["state"] == "unreadable"


def test_auto_discovered_products_get_their_own_rows():
    state = {
        "schema_version": 4,
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-future-set-elite-trainer-box/999001.html": _entry("Pokemon Trading Card Game: Future Set Elite Trainer Box", True, 79.99, since="2026-10-04T16:00:00+00:00"),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-tcg-future-set-ultra-premium-collection/1.html": _entry("Pokemon TCG: Future Set Ultra-Premium Collection", False, 179.99),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-30th-celebration-ultra-premium-collection-styles-may-vary/450768.html": _entry("Pokemon Trading Card Game: 30th Celebration Ultra-Premium Collection (Styles May Vary)", True, 399.99),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-pitch-black-elite-trainer-box/445744.html": _entry("Pokemon Trading Card Game: Pitch Black Elite Trainer Box", True, 84.99),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-future-set-elite-trainer-box-case/2.html": _entry("Pokemon Trading Card Game: Future Set Elite Trainer Box Case", True, 700.0),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/magic-the-gathering-elite-trainer-box/3.html": _entry("Magic: The Gathering Elite Trainer Box", True, 50.0),
        "gamestop::https://www.gamestop.com/toys-games/trading-cards/products/pokemon-trading-card-game-30th-celebration-figure-collection/4.html": _entry("Pokemon Trading Card Game: 30th Celebration Figure Collection", True, 40.0),
    }
    out = coverage.build_coverage(state, WATCH, {"auto:future set elite trainer box": {"market": 88.0, "updated_at": "2026-10-04T16:00:00+00:00", "product_id": 7}}, phrases=("elite trainer box", "ultra-premium collection"))
    autos = {r["label"]: r for r in out["rows"] if r.get("auto")}
    assert set(autos) == {"Future Set Elite Trainer Box", "Future Set Ultra-Premium Collection"}, "the 30th UPC is a watchlist row, so it never gets a second one: " + str(set(autos))
    assert coverage.nice_label("151 elite trainer box") == "151 Elite Trainer Box" and coverage.nice_label("ultra-premium collection") == "Ultra-Premium Collection"
    fut = autos["Future Set Elite Trainer Box"]
    assert fut["retailers"]["gamestop"]["state"] == "in_stock" and fut["retailers"]["gamestop"]["price"] == 79.99 and fut["market"] == 88.0
    assert fut["retailers"]["pokemoncenter"]["state"] == "unreadable"
    upc = next(r for r in autos.values() if "Ultra" in r["label"])
    assert upc["retailers"]["gamestop"]["state"] == "out"
    assert not [r for r in out["rows"] if r.get("auto") and "Pitch Black" in r["label"]], "a watchlist product never gets a second row"
    assert not [r for r in out["rows"] if r.get("auto") and ("Magic" in r["label"] or "Figure" in r["label"] or "Case" in r["label"])], "no cases, no other games, no other products"
    assert len(out["rows"]) == len(WATCH) + 2
    upc_rows = {r["id"]: r for r in out["rows"]}
    assert upc_rows["30th-upc-day"]["retailers"]["gamestop"]["price"] == 399.99


def test_watchlist_market_prices_come_from_the_right_tcgplayer_product():
    def fake(query):
        q = query.lower()
        for key, name in (("delta reign", "tcg_search_delta_reign.json"), ("chaos rising", "tcg_search_chaos_rising.json"), ("pitch black", "tcg_search_pitch_black.json"), ("30th celebration", "tcg_search_30th_etb.json"), ("destined rivals", "tcg_search_destined.json"), ("phantasmal", "tcg_search_phantasmal.json"), ("151", "tcg_search_151.json")):
            if key in q:
                return _fixture(name)
        return {"results": [{"totalResults": 0, "results": []}]}

    def worst_first(query):
        """The same real responses, but with the Case and Pokémon Center boxes ahead of the regular ETB."""
        payload = fake(query)
        rows = payload["results"][0]["results"]
        bad = [r for r in rows if "Case" in r["productName"] or "Pokemon Center" in r["productName"]]
        good = [r for r in rows if r not in bad]
        return {"results": [{"totalResults": len(rows), "results": bad + good}]}

    cache = {}
    refreshed = advisor.refresh_watchlist({"watchlist": WATCH}, cache, fetch=worst_first, now=datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc))
    assert refreshed == 7, refreshed  # (the UPC searches have no captured response here; they are covered below)
    assert cache["watch:delta-reign"]["market"] == 133.34 and cache["watch:delta-reign"]["name"] == "Delta Reign Elite Trainer Box", "not the Pokémon Center box ($531)"
    assert cache["watch:chaos-rising"]["market"] == 69.63 and cache["watch:pitch-black"]["market"] == 75.58
    assert cache["watch:30th-celebration"]["market"] == 156.92 and cache["watch:151"]["name"] == "151 Elite Trainer Box"
    assert cache["watch:delta-reign"]["label"] == "Delta Reign" and cache["watch:delta-reign"]["msrp"] is None
    assert "watch:perfect-order" not in cache, "no match, no price (never a guess)"

    def boom(q):
        raise RuntimeError("down")
    assert advisor.refresh_watchlist({"watchlist": WATCH}, {}, fetch=boom) == 0, "a TCGplayer outage refreshes nothing and never raises"


def test_cycle_writes_the_coverage_board_and_survives_its_failure():
    import tempfile
    import notify_new_listings as nn
    import refresh_live_hits as rl
    calls = []
    real = (monitor.main, rl.main, nn.main, coverage.main, prices_mod.main, monitor_loop.refresh_market)
    monitor.main = lambda discover=True, cycle=0: calls.append("stock")
    rl.main = lambda: calls.append("hits")
    nn.main = lambda: calls.append("new")
    prices_mod.main = lambda: None
    monitor_loop.refresh_market = lambda: None
    coverage.main = lambda: calls.append("coverage")
    try:
        monitor_loop.run_cycle(1)
        assert calls == ["stock", "coverage", "hits", "new"], calls
        calls.clear()

        def boom():
            raise RuntimeError("board broke")
        coverage.main = boom
        monitor_loop.run_cycle(1)
        assert calls == ["stock", "hits", "new"], "a broken board must not stop stock checks"
    finally:
        monitor.main, rl.main, nn.main, coverage.main, prices_mod.main, monitor_loop.refresh_market = real


GS = "https://www.gamestop.com/toys-games/trading-cards/products/"


class _GsHttp:
    """Fake GameStop: listing pages return tile markup; /products/-/<id>.html redirects to the canonical product page."""
    def __init__(self, listing, products, fail_listing=False):
        self.listing, self.products, self.calls, self.fail_listing = listing, products, [], fail_listing
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "/products/-/" in url:
            pid = url.rsplit("/", 1)[1].split(".")[0]
            if pid not in self.products:
                return _Resp(404, url, "")
            slug, title = self.products[pid]
            return _Resp(200, f"{GS}{slug}/{pid}.html?utm=x#reviews", f"<html><head><title>{title} | GameStop</title></head><body></body></html>")
        if self.fail_listing:
            return _Resp(403, url, "")
        return _Resp(200, url, self.listing)


def _tiles(*pids):
    return "".join(f'<div class="product-tile product-detail" data-pid="{p}"></div>' for p in pids)


def test_gamestop_listing_pids_come_from_the_real_markup():
    pids = gamestop_discovery.listing_pids(_page("gamestop_listing_tiles.html"))
    assert pids == ["450350", "452199", "448589", "447217", "450768"], pids
    assert gamestop_discovery.listing_pids("<html>no tiles</html>") == [] and gamestop_discovery.listing_pids(_tiles("1", "2", "1")) == ["1", "2"]


def test_only_pokemon_etbs_and_upcs_are_wanted():
    w = gamestop_discovery.is_wanted
    phrases = ["elite trainer box", "ultra-premium collection"]
    assert w("Pokemon Trading Card Game: Delta Reign Elite Trainer Box", GS + "pokemon-trading-card-game-delta-reign-elite-trainer-box/20037568.html", phrases)
    assert w("Pokémon TCG: 30th Celebration Ultra-Premium Collection - Night", GS + "pokemon-tcg-30th-celebration-ultra-premium-collection-night/1.html", phrases)
    assert not w("Pokemon Trading Card Game: 30th Celebration Figure Collection", GS + "pokemon-trading-card-game-30th-celebration-figure-collection/450350.html", phrases)
    assert not w("2025 Pokemon Mep Riolu Mega Evolution Elite Trainer Box PSA 10 Graded Card", "https://www.gamestop.com/graded-trading-cards/graded-cards/products/2025-pokemon-psa/PSA1.html", phrases), "graded singles are not boxes"
    assert not w("Magic: The Gathering Bloomburrow Elite Trainer Box", GS + "magic-the-gathering-elite-trainer-box/1.html", phrases), "Pokémon only"


def test_discovery_finds_a_brand_new_set_once_and_caches_every_id():
    config = {"discover_titles": ["elite trainer box"], "listing_pages": {"gamestop": ["https://www.gamestop.com/pokemon/new"]}}
    products = {
        "999001": ("pokemon-trading-card-game-future-set-elite-trainer-box", "Pokemon Trading Card Game: Future Set Elite Trainer Box"),
        "450350": ("pokemon-trading-card-game-30th-celebration-figure-collection", "Pokemon Trading Card Game: 30th Celebration Figure Collection"),
        "999002": ("pokemon-trading-card-game-another-set-elite-trainer-box", "Pokemon Trading Card Game: Another Set Elite Trainer Box"),
    }
    http = _GsHttp(_tiles("999001", "450350", "999002", "123456"), products)  # 123456 does not exist (404)
    cache = {}
    now = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    found = gamestop_discovery.discover(http, config, cache, monitor.canonical_url, now=now)
    assert found == [GS + "pokemon-trading-card-game-future-set-elite-trainer-box/999001.html", GS + "pokemon-trading-card-game-another-set-elite-trainer-box/999002.html"], found
    assert cache["999001"]["wanted"] is True and cache["450350"]["wanted"] is False and "123456" not in cache
    first_calls = len(http.calls)
    again = gamestop_discovery.discover(http, config, cache, monitor.canonical_url, now=now + timedelta(hours=1))
    resolved_again = [c for c in http.calls[first_calls:] if "/products/-/" in c]
    assert again == found and resolved_again == ["https://www.gamestop.com/products/-/123456.html"], "known ids are not resolved again (only the one id that never resolved is retried)"
    # a rejected id is rechecked after a week; a wanted one never needs it
    later = now + timedelta(days=8)
    before = len(http.calls)
    gamestop_discovery.discover(http, config, cache, monitor.canonical_url, now=later)
    assert [c.rsplit("/", 1)[1] for c in http.calls[before:] if "/products/-/" in c] == ["450350.html", "123456.html"]
    # resolve limit and failures never raise
    many = _GsHttp(_tiles(*[str(n) for n in range(100, 130)]), {str(n): (f"pokemon-x-{n}-elite-trainer-box", f"Pokemon X{n} Elite Trainer Box") for n in range(100, 130)})
    got = gamestop_discovery.discover(many, config, {}, monitor.canonical_url, now=now, fetch_limit=5)
    assert len(got) == 5, "at most fetch_limit unseen ids are resolved per call"
    assert gamestop_discovery.discover(_GsHttp("", {}, fail_listing=True), config, {}, monitor.canonical_url, now=now) == []

    class Boom:
        headers = {}
        def get(self, *a, **k):
            raise RuntimeError("network down")
    assert gamestop_discovery.discover(Boom(), config, {}, monitor.canonical_url, now=now) == [], "a network failure finds nothing and never raises"

    class ResolveBoom(_GsHttp):
        def get(self, url, **kwargs):
            if "/products/-/" in url:
                raise RuntimeError("product page timed out")
            return super().get(url, **kwargs)
    assert gamestop_discovery.discover(ResolveBoom(_tiles("1", "2"), {}), config, {}, monitor.canonical_url, now=now) == [], "a product page that times out is skipped, never raised"


def test_main_tracks_a_newly_discovered_etb_and_announces_it_once():
    import tempfile
    new_url = GS + "pokemon-trading-card-game-future-set-elite-trainer-box/999001.html"
    config = {"retailers": ["gamestop"], "keywords": [], "seed_urls": {"gamestop": []}, "discover_titles": ["elite trainer box"], "listing_pages": {"gamestop": ["https://www.gamestop.com/pokemon/new"]}}
    page = _page("gamestop_pitch_black_etb_available.html")

    class Gs(_GsHttp):
        def get(self, url, **kwargs):
            if url == new_url or "/products/-/" in url:
                self.calls.append(url)
                if "/products/-/" in url:
                    return _Resp(200, new_url + "#x", page.replace("Pitch Black", "Future Set"))
                return _Resp(200, new_url, page.replace("Pitch Black", "Future Set"))
            return super().get(url, **kwargs)

    gs = Gs(_tiles("999001"), {"999001": ("pokemon-trading-card-game-future-set-elite-trainer-box", "Pokemon Trading Card Game: Future Set Elite Trainer Box")})
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE, monitor.MARKET_FILE, monitor.PIDS_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "health.json", tmp / "market.json", tmp / "pids.json"
        monitor.CONFIG_FILE = tmp / "config.json"
        monitor.CONFIG_FILE.write_text(json.dumps(config))
        monitor.STATE_FILE.write_text(json.dumps({"schema_version": 4}))
        real = (monitor.requests.Session, monitor.alert, monitor.send_card, advisor.fetch_query)
        sent = []
        monitor.requests.Session = lambda: gs
        monitor.alert = lambda *a, **k: sent.append(a)
        monitor.send_card = lambda card: sent.append(card)
        advisor.fetch_query = _fake_tcg_search
        try:
            monitor.main(discover=True)
            state = json.loads(monitor.STATE_FILE.read_text())
            assert list(k for k in state if k != "schema_version") == ["gamestop::" + new_url], list(state)
            entry = state["gamestop::" + new_url]
            assert entry["in_stock"] is True and entry["new_announced"] is False, "a brand-new listing waits to be announced once, by the new-listing step"
            assert json.loads(monitor.PIDS_FILE.read_text())["999001"]["wanted"] is True
            monitor.main(discover=False)  # the fast pass keeps checking it without searching again
            assert gs.calls.count(new_url) >= 2
            # A discovery crash must never stop the stock checks of the listings already tracked.
            real_discover = gamestop_discovery.discover
            def crash(*a, **k):
                raise RuntimeError("discovery broke")
            gamestop_discovery.discover = crash
            try:
                before = gs.calls.count(new_url)
                monitor.main(discover=True)
                assert gs.calls.count(new_url) == before + 1, "the tracked listing is still checked when discovery crashes"
            finally:
                gamestop_discovery.discover = real_discover
        finally:
            monitor.requests.Session, monitor.alert, monitor.send_card, advisor.fetch_query = real


def test_an_alert_needs_two_agreeing_readings():
    import tempfile
    url = "https://www.target.com/p/-/A-7"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}}
    prior = {"schema_version": 4, f"target::{url}": {"pokemon": True, "title": "Pokemon ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    in_stock = '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"'
    sold_out = "<title>Pokemon ETB</title>Sold out online"
    blocked = "<title>Pokemon ETB</title><div id='px-captcha'></div>"

    def sequence(*bodies):
        it = iter(bodies)
        return lambda u: _Resp(200, u, next(it))

    # in stock then sold out a moment later (a flapping page): no alert, and the state follows the second reading
    with tempfile.TemporaryDirectory() as d:
        session, sent = _run_main(Path(d), config, dict(prior), {"target.com": sequence(in_stock, sold_out)})
        entry = json.loads((Path(d) / "state.json").read_text())[f"target::{url}"]
    assert sent == [] and entry["in_stock"] is False and not entry["confirmed"] and len(session.calls) == 2
    # the second reading is blocked: not confirmed, no alert
    with tempfile.TemporaryDirectory() as d:
        _, sent = _run_main(Path(d), config, dict(prior), {"target.com": sequence(in_stock, blocked)})
        entry = json.loads((Path(d) / "state.json").read_text())[f"target::{url}"]
    assert sent == [] and entry["in_stock"] is None
    # two agreeing readings: one alert, marked confirmed, and the proof says so
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        session, sent = _run_main(tmp, config, dict(prior), {"target.com": sequence(in_stock, in_stock)})
        state = json.loads((tmp / "state.json").read_text())
        assert len(sent) == 1 and sent[0]["confirmed"] is True and state[f"target::{url}"]["confirmed"] is True and len(session.calls) == 2
        proof = {f["name"]: f["value"] for f in notify.stock_embed(sent[0])["embeds"][0]["fields"]}["Proof"]
        assert "Confirmed by a second reading" in proof
        assert "Confirmed" not in {f["name"]: f["value"] for f in notify.stock_embed(dict(sent[0], confirmed=False))["embeds"][0]["fields"]}["Proof"]
        # staying in stock costs one request per check, not two, and keeps the confirmation
        session, sent = _run_main(tmp, config, state, {"target.com": sequence(in_stock)})
        assert len(session.calls) == 1 and sent == [] and json.loads((tmp / "state.json").read_text())[f"target::{url}"]["confirmed"] is True
    # a brand-new listing is confirmed too (first observation in stock)
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), config, {"schema_version": 4}, {"target.com": sequence(in_stock, sold_out)})
        entry = json.loads((Path(d) / "state.json").read_text())[f"target::{url}"]
    assert entry["in_stock"] is False and len(session.calls) == 2, "a first reading of in stock must also be confirmed before it is trusted"


def test_hot_listings_are_checked_every_cycle_and_the_rest_less_often():
    import tempfile
    hot, calm = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-30th-celebration-elite-trainer-box/1.html", "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-pitch-black-elite-trainer-box/2.html"
    config = {"retailers": ["gamestop"], "keywords": [], "seed_urls": {"gamestop": [hot, calm]}, "hot_matches": ["30th-celebration"], "slow_every": 3}
    sold = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")
    seen = {}
    for cycle in range(6):
        with tempfile.TemporaryDirectory() as d:
            session, _ = _run_main(Path(d), config, {"schema_version": 4}, {"gamestop.com": sold}, cycle=cycle)
        seen[cycle] = session.calls
    for cycle in (1, 2, 4, 5):
        assert seen[cycle] == [hot], f"cycle {cycle}: only the hot listing is checked"
    for cycle in (0, 3):
        assert seen[cycle] == [hot, calm], f"cycle {cycle}: everything is checked"
    # with no hot list configured, everything is checked every cycle
    plain = {k: v for k, v in config.items() if k not in ("hot_matches",)}
    with tempfile.TemporaryDirectory() as d:
        session, _ = _run_main(Path(d), plain, {"schema_version": 4}, {"gamestop.com": sold}, cycle=1)
    assert session.calls == [hot, calm], "without a hot list there is no slow rhythm: everything is checked every cycle"


def _browser_rows():
    return {r["name"]: r for r in json.loads((ROOT / "tests" / "fixtures" / "browser_snapshot_2026-10-04.json").read_text(encoding="utf-8"))}


def test_browser_reader_decides_from_the_visible_buy_button_on_real_pages():
    rows = _browser_rows()
    c = browser_reader.classify_rendered
    # Captured from GitHub's runner on 2026-10-04 with a real headless browser.
    assert c(rows["bestbuy_30th_etb"]) == (False, "ok", None), "a disabled Unavailable button is not in stock (Best Buy's JSON-LD said InStock)"
    assert c(rows["bestbuy_pitch_black_etb"]) == (True, "ok", "browser"), "an enabled, visible Add to cart is in stock"
    assert c(rows["target_30th_etb"]) == (None, "blocked", None), "Target's press-and-hold wall is blocked, even though a hidden Add to cart sits behind it"
    assert c(rows["walmart_30th_etb"]) == (None, "blocked", None)
    assert c(rows["gamestop_pitch_black_etb"]) == (None, "blocked", None), "Cloudflare's 'Attention Required' page is a block page"
    assert c(rows["pokemoncenter_home"]) == (None, "blocked", None) and c(rows["pokemoncenter_tcg"]) == (None, "blocked", None), "an empty page is a block page, never 'out of stock'"
    # edge cases
    btn = lambda label, visible=True, enabled=True: {"label": label, "visible": visible, "enabled": enabled}
    page = lambda *buttons, **kw: {"walls": [], "title": "Pokemon ETB", "body_chars": 3000, "buttons": list(buttons), **kw}
    assert c(page(btn("Add to cart"), btn("Sold Out"))) == (True, "ok", "browser"), "an enabled buy button wins over another variant's Sold Out"
    assert c(page(btn("Add to cart", enabled=False))) == (False, "ok", None), "a disabled Add to cart is not stock"
    assert c(page(btn("Add to cart", visible=False))) == (None, "no_signal", None), "a hidden button is not evidence of anything"
    assert c(page(btn("Coming Soon"))) == (False, "ok", None) and c(page(btn("Notify Me"))) == (False, "ok", None) and c(page(btn("Check Stores"))) == (False, "ok", None)
    assert c(page()) == (None, "no_signal", None), "no buy button and a real page: unknown, never a guess"
    assert c(page(btn("Add to cart"), walls=["px-captcha"])) == (None, "blocked", None)
    assert c({"error": "timeout"}) == (None, "error", None)
    assert c(page(btn("Add to cart"), title="Access Denied")) == (None, "blocked", None), "a wall named in the title is a wall"


def test_browser_stores_are_read_by_the_browser_and_the_rest_over_http():
    import tempfile
    bb, gs = "https://www.bestbuy.com/product/pokemon-pitch-black-elite-trainer-box/JJG1", "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-pitch-black-elite-trainer-box/1.html"
    config = {"retailers": ["bestbuy", "gamestop"], "keywords": [], "seed_urls": {"bestbuy": [bb], "gamestop": [gs]}, "browser_retailers": ["bestbuy"]}
    sold = lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")

    class FakeBrowser:
        def __init__(self, stock):
            self.calls, self.stock = [], stock
        def check(self, url):
            self.calls.append(url)
            return {"stock": self.stock, "title": "Pokemon Pitch Black Elite Trainer Box", "posted_at": None, "http_status": 200, "reason": "ok", "signal": "browser" if self.stock else None, "price": 49.99}

    prior = {"schema_version": 4, f"bestbuy::{bb}": {"pokemon": True, "title": "Pokemon Pitch Black ETB", "in_stock": False, "last_seen": "2026-10-03T00:00:00+00:00"}}
    real = monitor.BROWSER
    try:
        monitor.BROWSER = FakeBrowser(True)
        with tempfile.TemporaryDirectory() as d:
            session, sent = _run_main(Path(d), config, prior, {"gamestop.com": sold, "bestbuy.com": sold})
            state = json.loads((Path(d) / "state.json").read_text())
        assert monitor.BROWSER.calls == [bb, bb], "Best Buy goes through the browser (twice: reading plus confirmation)"
        assert session.calls == [gs], "GameStop still goes over plain HTTP, and Best Buy never does"
        assert len(sent) == 1 and sent[0]["signal"] == "browser" and sent[0]["confirmed"] is True and sent[0]["price"] == 49.99
        assert "real browser" in {f["name"]: f["value"] for f in notify.stock_embed(sent[0])["embeds"][0]["fields"]}["Proof"]
        # no browser available: Best Buy falls back to plain HTTP (and a timeout there is simply unknown)
        monitor.BROWSER = None
        with tempfile.TemporaryDirectory() as d:
            session, _ = _run_main(Path(d), config, prior, {"gamestop.com": sold, "bestbuy.com": sold})
        assert session.calls == [bb, gs]
    finally:
        monitor.BROWSER = real


def test_the_browser_starts_only_when_enabled_and_failure_is_harmless():
    import os
    real_env, real_browser = os.environ.get("POKEPING_BROWSER"), monitor.BROWSER
    try:
        os.environ.pop("POKEPING_BROWSER", None)
        monitor.BROWSER = None
        started = []
        real_cls = browser_reader.BrowserReader
        class Counting:
            def __init__(self):
                started.append(1)
        browser_reader.BrowserReader = Counting
        try:
            monitor_loop.start_browser()
            assert monitor.BROWSER is None and started == [], "off unless POKEPING_BROWSER=1: the reader is not even created"
            os.environ["POKEPING_BROWSER"] = "1"
            monitor_loop.start_browser()
            assert isinstance(monitor.BROWSER, Counting) and started == [1], "with the switch on it starts, once"
            monitor_loop.start_browser()
            assert started == [1], "and an already started browser is not started again"
        finally:
            browser_reader.BrowserReader = real_cls
            monitor.BROWSER = None
        class Broken:
            def __init__(self):
                raise RuntimeError("no chromium installed")
        browser_reader.BrowserReader = Broken
        try:
            monitor_loop.start_browser()
        finally:
            browser_reader.BrowserReader = real_cls
        assert monitor.BROWSER is None, "a missing browser must not stop the watcher"
        cfg = json.loads((ROOT / "config" / "search_config.json").read_text(encoding="utf-8"))
        assert cfg["browser_retailers"] == ["bestbuy"] and len(cfg["seed_urls"]["bestbuy"]) >= 6
        for url in cfg["seed_urls"]["bestbuy"]:
            assert monitor.retailer_url_is_valid("bestbuy", url), url
        workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
        assert "playwright install --with-deps chromium" in workflow and 'POKEPING_BROWSER: "1"' in workflow
        install = workflow[workflow.index("Install the browser"):workflow.index("Watch stock")]
        assert "continue-on-error: true" in install, "a failed browser install must never stop the watcher"
    finally:
        monitor.BROWSER = real_browser
        if real_env is None:
            os.environ.pop("POKEPING_BROWSER", None)
        else:
            os.environ["POKEPING_BROWSER"] = real_env


def test_the_real_browser_reads_real_pages():
    """Drives a real headless Chromium against a local web server that behaves like the stores seen on 2026-10-04."""
    import os
    import threading
    import tempfile
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        assert os.environ.get("POKEPING_REQUIRE_BROWSER") != "1", "CI must have Playwright installed"
        print("  (skipped: Playwright is not installed here)")
        return
    filler = "<p>" + "Pokemon Elite Trainer Box product details. " * 40 + "</p>"
    pages = {
        "buyable.html": f"<html><head><title>Pokemon ETB - Best Buy</title></head><body><h1>Pokemon ETB</h1><span>$49.99</span>{filler}<button>Add to cart</button></body></html>",
        "unavailable.html": f"<html><head><title>Pokemon 30th ETB - Best Buy</title></head><body><span>$284.99</span>{filler}<button disabled>Unavailable</button></body></html>",
        "wall.html": f"<html><head><title>Target</title></head><body><div id='px-captcha'></div><h1>Quick verification</h1><p>Press &amp; hold to confirm you're a human</p>{filler}<button style='display:none'>Add to cart</button></body></html>",
        "later.html": f"<html><head><title>Pokemon ETB</title></head><body>{filler}<div id='slot'>loading</div><script>setTimeout(function(){{document.getElementById('slot').innerHTML='<button>Add to cart</button>';}}, 700);</script></body></html>",
        "sku.html": f"<html><head><title>Pokemon ETB - Best Buy</title></head><body>{filler}<div data-sku-id=\"6678361\"></div><button>Add to cart</button></body></html>",
        "skustub.html": f"<html><head><title>Pokemon ETB - Best Buy</title></head><body>{filler}<script>var x = {{\"skuId\":\"6685559\"}};</script></body></html>",
        "market.html": f"<html><head><title>Pokemon ETB - Best Buy</title></head><body>{filler}<div>An account is required to purchase this item.</div><button>Add to cart</button><div>Sold &amp; shipped by</div><div>Shopville Inc</div><div>4.31</div><div>More options from Marketplace sellers</div><div>New</div><div>$96.99 - $155.94</div></body></html>",
        "firstparty.html": f"<html><head><title>Pokemon ETB - Best Buy</title></head><body>{filler}<button>Add to cart</button><div>Sold &amp; shipped by</div><div>Best Buy</div></body></html>",
        "blank.html": "<html><head></head><body></body></html>",
        "many.html": "<html><head><title>Heavy store page</title></head><body>" + filler + "".join(f"<button>Filter option {n}</button>" for n in range(600)) + "<div id='slot'></div><script>setTimeout(function(){document.getElementById('slot').innerHTML='<button>Add to cart</button>';}, 400);</script></body></html>",
        "hidden.html": f"<html><head><title>Pokemon ETB</title></head><body>{filler}<button style='display:none'>Add to cart</button></body></html>",
        "sold.html": f"<html><head><title>Pokemon ETB</title></head><body>{filler}<button>Sold Out</button><button>Add to cart</button><script>document.querySelectorAll('button')[1].disabled = true;</script></body></html>",
    }
    with tempfile.TemporaryDirectory() as d:
        for name, html_text in pages.items():
            (Path(d) / name).write_text(html_text, encoding="utf-8")

        class Quiet(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=d, **k)
            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}/"
        reader = browser_reader.BrowserReader(wait_ms=1500)
        try:
            check = lambda name: reader.check(base + name)
            got = check("buyable.html")
            assert (got["stock"], got["reason"], got["signal"], got["price"]) == (True, "ok", "browser", 49.99), got
            assert got["title"] == "Pokemon ETB - Best Buy" and got["http_status"] == 200
            got = check("unavailable.html")
            assert (got["stock"], got["reason"], got["price"]) == (False, "ok", 284.99), got
            got = check("wall.html")
            assert (got["stock"], got["reason"]) == (None, "blocked"), "a verification wall is blocked even with a hidden Add to cart behind it"
            got = check("later.html")
            assert (got["stock"], got["signal"]) == (True, "browser"), "a buy button that JavaScript adds after load is seen (the Target case)"
            got = check("blank.html")
            assert (got["stock"], got["reason"]) == (None, "blocked"), "a blank page is a block page, never out of stock"
            import time as _time
            reader.wait_ms = 9000
            started = _time.monotonic()
            got = reader.check(base + "many.html")
            took = _time.monotonic() - started
            reader.wait_ms = 1500
            assert got["stock"] is True and took < 5, f"600 buttons and a late buy button must be read in one quick pass (took {took:.1f}s of a 9 s allowance)"
            bb = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-chaos-rising-elite-trainer-box/JJG2TL34RT"
            row = reader.snapshot(base + "market.html")   # the page is served locally, so classify it as the Best Buy page it stands in for
            assert row["seller"] == "Shopville Inc", f"the seller named after the buy button is read from the rendered page: {row['seller']!r}"
            assert browser_reader.classify_rendered({**row, "url": bb}) == (False, "marketplace_only", None), "a marketplace seller's Add to cart is not Best Buy restocking"
            row = reader.snapshot(base + "firstparty.html")
            assert row["seller"] == "Best Buy" and browser_reader.classify_rendered({**row, "url": bb}) == (True, "ok", "browser"), "Best Buy as the seller is a real restock"
            assert reader.snapshot(base + "buyable.html")["seller"] is None, "a page that names no seller gives none"
            got = check("sku.html")
            assert got["stock"] is True and got["sku"] == "6678361", "the SKU is read from the page next to the buy button"
            got = check("skustub.html")
            assert got["stock"] is None and got["sku"] == "6685559", "a half-loaded page still yields the SKU, but never counts as stock"
            assert check("buyable.html")["sku"] is None
            got = check("hidden.html")
            assert (got["stock"], got["reason"]) == (None, "no_signal"), "a buy button nobody can see is not stock"
            got = check("sold.html")
            assert got["stock"] is False, "Sold Out plus a disabled Add to cart is not stock"
            got = reader.check("http://127.0.0.1:1/nothing")
            assert (got["stock"], got["reason"]) == (None, "error"), "an unreachable page is an error, never a reading"
            # the same reader keeps working after an error
            assert check("buyable.html")["stock"] is True
        finally:
            reader.close()
            server.shutdown()


class _Clock2:
    """A fake `time` for monitor: monotonic() only moves when the test (or a slow read) says so."""
    def __init__(self):
        self.t = 1000.0
    def monotonic(self):
        return self.t
    def sleep(self, seconds):
        pass


class _SlowBrowser:
    def __init__(self, clock, seconds, stock=False, reason="ok"):
        self.clock, self.seconds, self.stock, self.reason, self.calls = clock, seconds, stock, reason, []
    def check(self, url):
        self.calls.append(url)
        self.clock.t += self.seconds
        return {"stock": None if self.reason != "ok" else self.stock, "title": "Pokemon ETB", "posted_at": None, "http_status": 200, "reason": self.reason, "signal": None, "price": 49.99}


def _browser_config(urls):
    return {"retailers": ["bestbuy"], "keywords": [], "seed_urls": {"bestbuy": urls}, "browser_retailers": ["bestbuy"], "hot_matches": ["30th-celebration"],
            "browser_hot_every": 4, "browser_slow_every": 20, "cycle_budget_seconds": 75}


BB_HOT = "https://www.bestbuy.com/product/pokemon-30th-celebration-elite-trainer-box/JJG1"
BB_CALM = "https://www.bestbuy.com/product/pokemon-pitch-black-elite-trainer-box/JJG2"


def test_browser_stores_are_read_gently():
    import tempfile
    clock = _Clock2()
    real = (monitor.time, monitor.BROWSER, dict(monitor._BACKOFF_UNTIL))
    monitor.time = clock
    try:
        monitor._BACKOFF_UNTIL.clear()
        monitor.BROWSER = _SlowBrowser(clock, 1)
        reads = {}
        for cycle in range(0, 41):
            before = len(monitor.BROWSER.calls)
            with tempfile.TemporaryDirectory() as d:
                _run_main(Path(d), _browser_config([BB_CALM, BB_HOT]), {"schema_version": 4}, {}, cycle=cycle)
            reads[cycle] = monitor.BROWSER.calls[before:]
        assert [c for c, urls in reads.items() if BB_HOT in urls] == [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40], "hot Best Buy pages are read about every 2 minutes (every 4th 30 s cycle)"
        assert [c for c, urls in reads.items() if BB_CALM in urls] == [0, 20, 40], "calm Best Buy pages about every 10 minutes"
        assert reads[0] == [BB_HOT, BB_CALM], "hot first"
    finally:
        monitor.time, monitor.BROWSER = real[0], real[1]
        monitor._BACKOFF_UNTIL.clear()
        monitor._BACKOFF_UNTIL.update(real[2])


def test_a_failing_search_engine_is_not_asked_again_for_a_while():
    clock = _Clock2()
    real = (monitor.time, dict(monitor._ENGINE_DOWN_UNTIL))
    monitor.time = clock
    monitor._ENGINE_DOWN_UNTIL.clear()
    try:
        class Http:
            def __init__(self):
                self.calls = []
            def get(self, url, **kw):
                self.calls.append(url.split("/")[2])
                if "duckduckgo" in url:
                    raise OSError("Max retries exceeded")
                return _Resp(200, url, '<a href="https://www.gamestop.com/toys-games/trading-cards/products/pokemon-pitch-black-elite-trainer-box/445744.html">x</a>')
        http = Http()
        first = monitor.fallback_search(http, "gamestop", "pokemon pitch black")
        assert first and http.calls == ["html.duckduckgo.com", "www.bing.com"], "the first search tries DuckDuckGo, it fails, Bing answers"
        monitor.fallback_search(http, "gamestop", "pokemon prismatic")
        monitor.fallback_search(http, "gamestop", "pokemon white flare")
        assert http.calls.count("html.duckduckgo.com") == 1 and http.calls.count("www.bing.com") == 3, "after one failure DuckDuckGo is left alone (it used to cost 7 s per search, all day)"
        clock.t += monitor.ENGINE_COOLDOWN_SECONDS + 1
        monitor.fallback_search(http, "gamestop", "pokemon later")
        assert http.calls.count("html.duckduckgo.com") == 2, "after the cool-down it is tried again"
        # an HTTP refusal (not just an exception) also starts the cool-down
        class Refuse:
            def __init__(self):
                self.calls = []
            def get(self, url, **kw):
                self.calls.append(url.split("/")[2])
                return _Resp(403, url, "denied")
        monitor._ENGINE_DOWN_UNTIL.clear()
        refuse = Refuse()
        monitor.fallback_search(refuse, "gamestop", "a")
        monitor.fallback_search(refuse, "gamestop", "b")
        assert refuse.calls == ["html.duckduckgo.com", "www.bing.com"], f"a 403 from both engines silences both: {refuse.calls}"
    finally:
        monitor.time = real[0]
        monitor._ENGINE_DOWN_UNTIL.clear()
        monitor._ENGINE_DOWN_UNTIL.update(real[1])


def test_keyword_discovery_stops_at_its_time_budget():
    import tempfile
    clock = _Clock2()
    real = (monitor.time, monitor.discover_products)
    monitor.time = clock
    searched = []
    try:
        def slow_discover(http, retailer, keyword, timeout):
            searched.append(keyword)
            clock.t += 30   # every search is slow
            return []
        config = {"retailers": ["gamestop"], "keywords": [f"kw{n}" for n in range(20)], "seed_urls": {"gamestop": []}, "discovery_budget_seconds": 100}
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE, monitor.MARKET_FILE, monitor.PIDS_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "health.json", tmp / "market.json", tmp / "pids.json"
            monitor.CONFIG_FILE = tmp / "config.json"
            monitor.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")
            monitor.STATE_FILE.write_text(json.dumps({"schema_version": 4}), encoding="utf-8")
            real_session, real_gs = monitor.requests.Session, monitor.gamestop_discovery.discover
            monitor.requests.Session = lambda: _FakeSession({})
            monitor.gamestop_discovery.discover = lambda *a, **k: []
            monitor.discover_products = slow_discover
            try:
                monitor.main(discover=True, cycle=0)
            finally:
                monitor.requests.Session, monitor.gamestop_discovery.discover = real_session, real_gs
        assert 3 <= len(searched) <= 5, f"20 slow keywords but only about 100 s worth are searched per cycle, got {len(searched)}"
    finally:
        monitor.time, monitor.discover_products = real


def test_an_item_that_is_in_stock_is_checked_at_the_hot_rhythm():
    """Live online hides an item not checked for 5 minutes, and a sell-out should show quickly: once something is in stock it is re-read every
    ~2 minutes (browser) / every cycle (HTTP) even if it is a calm product that is normally read every 10 minutes / every 3rd cycle."""
    import tempfile
    clock = _Clock2()
    real = (monitor.time, monitor.BROWSER, dict(monitor._BACKOFF_UNTIL))
    monitor.time = clock
    try:
        monitor._BACKOFF_UNTIL.clear()
        key = f"bestbuy::{BB_CALM}"
        for previous_stock, cycle, expect in ((False, 4, 0), (True, 4, 1), (True, 5, 0), (True, 8, 1), (None, 4, 0)):
            monitor.BROWSER = _SlowBrowser(clock, 1)
            state = {"schema_version": 4, key: {"pokemon": True, "title": "Pokemon ETB", "in_stock": previous_stock, "last_seen": "2026-10-03T00:00:00+00:00"}}
            with tempfile.TemporaryDirectory() as d:
                _run_main(Path(d), _browser_config([BB_CALM]), state, {}, cycle=cycle)
            assert len(monitor.BROWSER.calls) == expect, f"previous in_stock={previous_stock} at cycle {cycle}: expected {expect} read(s), got {len(monitor.BROWSER.calls)}"
    finally:
        monitor.time, monitor.BROWSER = real[0], real[1]
        monitor._BACKOFF_UNTIL.clear()
        monitor._BACKOFF_UNTIL.update(real[2])
    # plain HTTP stores: a calm in-stock listing is read every cycle, a calm out-of-stock one every 3rd
    url = "https://www.target.com/p/-/A-77"
    config = {"retailers": ["target"], "keywords": [], "seed_urls": {"target": [url]}, "hot_matches": ["30th-celebration"], "slow_every": 3}
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-03T00:00:00+00:00"}
    for stock, cycle, expect in ((False, 1, 0), (False, 3, 1), (True, 1, 1), (True, 2, 1)):
        with tempfile.TemporaryDirectory() as d:
            session, _ = _run_main(Path(d), config, {"schema_version": 4, f"target::{url}": {**base, "in_stock": stock}}, {"target.com": lambda u: _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")}, cycle=cycle)
        reads = [c for c in session.calls if "target.com/p/-/A-77" in c]
        assert len(reads) >= expect and (expect == 0) == (len(reads) == 0), f"HTTP, previous in_stock={stock}, cycle {cycle}: {len(reads)} read(s)"


def test_a_slow_cycle_stops_at_its_budget_and_a_failing_store_is_left_alone():
    import tempfile
    clock = _Clock2()
    urls = [f"https://www.bestbuy.com/product/pokemon-30th-celebration-elite-trainer-box-{n}/JJG{n}" for n in range(5)]
    real = (monitor.time, monitor.BROWSER, dict(monitor._BACKOFF_UNTIL))
    monitor.time = clock
    try:
        monitor._BACKOFF_UNTIL.clear()
        # Each read takes 40 s and the budget is 75 s: the budget is checked before every read (at 0 s, 40 s, 80 s), so two fit.
        monitor.BROWSER = _SlowBrowser(clock, 40)
        with tempfile.TemporaryDirectory() as d:
            _run_main(Path(d), _browser_config(urls), {"schema_version": 4}, {}, cycle=0)
        assert len(monitor.BROWSER.calls) == 2, monitor.BROWSER.calls
        # Two failed reads in a row put the store on a 5 minute back-off; nothing is read until it passes.
        clock.t = 5000.0
        monitor.BROWSER = _SlowBrowser(clock, 1, reason="error")
        with tempfile.TemporaryDirectory() as d:
            _run_main(Path(d), _browser_config(urls), {"schema_version": 4}, {}, cycle=0)
        assert len(monitor.BROWSER.calls) == 2, "the second failure triggers the back-off, so the remaining pages are not tried"
        clock.t += 100
        monitor.BROWSER = _SlowBrowser(clock, 1)
        with tempfile.TemporaryDirectory() as d:
            _run_main(Path(d), _browser_config(urls), {"schema_version": 4}, {}, cycle=0)
        assert monitor.BROWSER.calls == [], "still backing off after 100 s"
        clock.t += 300
        monitor.BROWSER = _SlowBrowser(clock, 1)
        with tempfile.TemporaryDirectory() as d:
            _run_main(Path(d), _browser_config(urls), {"schema_version": 4}, {}, cycle=0)
        assert len(monitor.BROWSER.calls) == 5, "after the back-off the store is read again"
        # One failure followed by a success resets the count (no back-off).
        class Flaky(_SlowBrowser):
            def check(self, url):
                self.reason = "error" if len(self.calls) == 0 else "ok"
                return super().check(url)
        monitor._BACKOFF_UNTIL.clear()
        monitor.BROWSER = Flaky(clock, 1)
        with tempfile.TemporaryDirectory() as d:
            _run_main(Path(d), _browser_config(urls), {"schema_version": 4}, {}, cycle=0)
        assert len(monitor.BROWSER.calls) == 5 and not monitor._BACKOFF_UNTIL.get("bestbuy"), "an isolated failure never triggers a back-off"
    finally:
        monitor.time, monitor.BROWSER = real[0], real[1]
        monitor._BACKOFF_UNTIL.clear()
        monitor._BACKOFF_UNTIL.update(real[2])


def test_progress_is_saved_after_each_store():
    import tempfile

    class Stop(BaseException):
        pass

    gs = "https://www.gamestop.com/toys-games/trading-cards/products/pokemon-pitch-black-elite-trainer-box/1.html"
    config = {"retailers": ["gamestop", "target"], "keywords": [], "seed_urls": {"gamestop": [gs], "target": ["https://www.target.com/p/-/A-1"]}}

    def respond(u):
        if "target.com" in u:
            raise Stop()  # the second store blows up mid-run
        return _Resp(200, u, "<title>Pokemon ETB</title>Sold out online")

    with tempfile.TemporaryDirectory() as d:
        try:
            _run_main(Path(d), config, {"schema_version": 4}, {"gamestop.com": respond, "target.com": respond})
            raise AssertionError("expected the simulated crash")
        except Stop:
            pass
        state = json.loads((Path(d) / "state.json").read_text())
        health = json.loads((Path(d) / "health.json").read_text())
    assert f"gamestop::{gs}" in state and state[f"gamestop::{gs}"]["in_stock"] is False, "GameStop's fresh reading was saved before Target ran"
    assert health["gamestop"]["readable"] == 1 and "target" not in health


def test_the_best_buy_sku_reaches_the_alert_and_is_remembered_in_state():
    import tempfile
    pb = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box/JJG2TL8J45"
    other = "https://www.bestbuy.com/product/pokemon-trading-card-game-mega-evolution-chaos-rising-elite-trainer-box/JJG2TL34RT"
    config = {"retailers": ["bestbuy"], "keywords": [], "seed_urls": {"bestbuy": [pb, other]}, "bestbuy_skus": {"JJG2TL8J45": "6678361"}}
    in_stock = lambda u: _Resp(200, u, '<title>Pokemon ETB</title>"availability":"https://schema.org/InStock"')
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-03T00:00:00+00:00", "in_stock": False}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        state = {"schema_version": 4, f"bestbuy::{pb}": dict(base), f"bestbuy::{other}": {**base, "sku": "6600001"}}
        _, sent = _run_main(tmp, config, state, {"bestbuy.com": in_stock})
        cards = {c["url"]: c for c in sent if isinstance(c, dict)}
        assert cards[pb]["add_url"] == "https://api.bestbuy.com/click/-/6678361/cart", "the verified SKU from the config reaches the alert"
        assert cards[other]["add_url"] == "https://api.bestbuy.com/click/-/6600001/cart", "a SKU remembered from an earlier page read reaches the alert"
        saved = json.loads((tmp / "state.json").read_text())
        assert saved[f"bestbuy::{pb}"]["sku"] == "6678361" and saved[f"bestbuy::{other}"]["sku"] == "6600001", "the SKU is kept in state for the next stay"


def test_store_hours_flip_to_open_and_closed_live_in_a_real_browser():
    """Walmart opens at 6:00 AM Eastern: a page that is already open must flip from Closed to Open with no reload (fake clock)."""
    import os
    import threading
    from datetime import datetime, timezone
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    try:
        import playwright.sync_api as pw_api
    except ImportError:
        assert os.environ.get("POKEPING_REQUIRE_BROWSER") != "1", "CI must have Playwright installed"
        print("  (skipped: Playwright is not installed here)")
        return

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT / "docs"), **k)
        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/"
    walmart = "Walmart Supercenter Kearny"
    try:
        with pw_api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                def status_of(page, name):
                    return page.evaluate("(n) => { const a = [...document.querySelectorAll('article.stop')].find(x => x.querySelector('.name').textContent.includes(n)); return a ? a.querySelector('.status-text').textContent : null; }", name)

                page = browser.new_page()
                page.clock.install(time=datetime(2026, 10, 5, 9, 59, 50, tzinfo=timezone.utc))   # Monday 5:59:50 AM in New Jersey
                page.goto(base + "route.html")
                page.wait_for_selector("article.stop", timeout=15000)
                before = status_of(page, walmart)
                assert before and before.startswith("Closed"), f"before 6:00 AM Walmart is closed, got {before!r}"
                page.clock.run_for(20000)   # 6:00:10 AM, with no reload
                after = status_of(page, walmart)
                assert after and after.startswith("Open now"), f"after 6:00 AM the same page must show open, got {after!r}"
                page.clock.run_for(17 * 3600 * 1000)   # 11:00:10 PM: closing time has passed
                late = status_of(page, walmart)
                assert late and late.startswith("Closed"), f"after 11:00 PM it must flip back to closed, got {late!r}"
                # TCGDUCKHUNTER is closed all Sunday
                sunday = browser.new_page()
                sunday.clock.install(time=datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc))
                sunday.goto(base + "route.html")
                sunday.wait_for_selector("article.stop", timeout=15000)
                duck = status_of(sunday, "TCGDUCKHUNTER")
                assert duck is None or duck.startswith("Closed"), f"TCGDUCKHUNTER is closed on Sunday, got {duck!r}"
            finally:
                browser.close()
    finally:
        server.shutdown()


def test_the_map_re_reads_the_clock_by_itself():
    map_js = (ROOT / "docs" / "js" / "map.js").read_text(encoding="utf-8")
    route = (ROOT / "docs" / "route.html").read_text(encoding="utf-8")
    assert "setInterval(refreshHours, 15000)" in map_js, "the map must re-evaluate open/closed on a timer"
    assert 'addEventListener("visibilitychange", () => { if (!document.hidden) refreshHours(); })' in map_js, "a woken tab must catch up at once"
    assert "setInterval(()=>{if(stores.length)build(!!legs)},15000)" in route, "the route page must re-evaluate open/closed on a timer"
    assert "setPopupContent" in map_js, "an open popup must update in place"


def test_store_hours_data_is_complete_and_sourced():
    stores = json.loads((ROOT / "docs" / "stores.json").read_text(encoding="utf-8"))["stores"]
    for s in stores:
        assert "open" not in s and "close" not in s, f"{s['id']} carries a single open/close pair that ignores the weekly schedule"
        assert s.get("hours_source") and s.get("hours_checked"), f"{s['id']} must say where its hours came from and when they were checked"
        assert re.search(r"\bMon", s["hours"]) and re.search(r"\b(AM|PM)\b", s["hours"]), f"{s['id']} has no readable weekly schedule: {s['hours']}"
    by = {s["id"]: s for s in stores}
    assert "Sun closed" in by["tcgduckhunter"]["hours"] and "Sun closed" in by["bestbuy-paramus"]["hours"] and "Sun closed" in by["target-paramus"]["hours"]
    assert by["target-clifton"]["hours"] == "Mon–Sun 8:00 AM–11:00 PM"
    assert "Mon closed" in by["bodega-hoboken"]["hours"]


def test_page_assets_are_versioned_so_a_browser_never_mixes_releases():
    """A cached old common.js next to a new route.html broke the deployed Route page; every script and stylesheet URL carries a content hash."""
    sys.path.insert(0, str(ROOT / "tools"))
    import stamp_assets
    assert stamp_assets.main(check=True) == 0, "run `python tools/stamp_assets.py` after changing anything in docs/js or docs/css"
    for page in sorted((ROOT / "docs").glob("*.html")):
        html_text = page.read_text(encoding="utf-8")
        local = re.findall(r'(?:src|href)="((?:css|js)/[^"]+)"', html_text)
        assert local, f"{page.name} loads no local assets?"
        for ref in local:
            assert re.fullmatch(r"(?:css|js)/[A-Za-z0-9_.-]+\.(?:css|js)\?v=[0-9a-f]{8}", ref), f"{page.name}: {ref} has no version stamp"


def test_ci_runs_the_end_to_end_test():
    workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
    step = workflow[workflow.index("Run the end-to-end test"):]
    step = step[:step.index("setup-node")]
    assert "python -u tests/test_end_to_end.py" in step and 'POKEPING_REQUIRE_BROWSER: "1"' in step, "CI must run the end-to-end test with a real browser required"


def test_the_watcher_never_shares_a_concurrency_group_with_test_runs():
    """GitHub keeps one PENDING run per group. The watcher's queued handover shared a group with push test runs, so every merge replaced it
    (cancelled) and the watcher chain broke: the data went stale after each merge until the 30-minute cron."""
    workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
    block = workflow[workflow.index("concurrency:"):workflow.index("jobs:")]
    group = re.search(r"group: (.+)", block).group(1)
    assert "'schedule'" in group and "'workflow_dispatch'" in group and "pokeping-watcher" in group, "the watcher (schedule / dispatch) has its own group"
    assert "pokeping-tests-" in group and "github.ref" in group, "test runs (push / pull_request) use a different group per ref"
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in block, "a running watcher is never cancelled by a newer run; only superseded PR test runs are"
    names = re.findall(r"^\s+group: (.+)$", "\n".join((p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml"))), re.M)
    assert len([n for n in names if "pokeping-watcher" in n]) == 1, "no other workflow joins the watcher's group"


def test_the_deploy_verifier_passes_a_good_site_and_fails_a_broken_one():
    import os
    import shutil
    import subprocess
    import tempfile
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        assert os.environ.get("POKEPING_REQUIRE_BROWSER") != "1", "CI must have Playwright installed"
        print("  (skipped: Playwright is not installed here)")
        return

    def verify(site_dir):
        class Quiet(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(site_dir), **k)
            def log_message(self, *a):
                pass
        server = HTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            return subprocess.run([sys.executable, str(ROOT / "tools" / "verify_deployed.py"), f"http://127.0.0.1:{server.server_port}/"], capture_output=True, text=True, timeout=240)
        finally:
            server.shutdown()

    good = verify(ROOT / "docs")
    # the verifier compares with the repo's docs/, so a good copy of docs/ passes and any difference or breakage fails
    assert good.returncode == 0, f"a good site must verify: {good.stdout}{good.stderr}"
    with tempfile.TemporaryDirectory() as d:
        broken = Path(d) / "docs"
        shutil.copytree(ROOT / "docs", broken)
        (broken / "js" / "common.js").write_text("// emptied", encoding="utf-8")
        bad = verify(broken)
        assert bad.returncode == 1 and "FAIL" in bad.stdout, f"a site with a broken common.js must fail: {bad.stdout}"


if __name__ == "__main__":
    test_retailer_urls()
    test_pokemon_detection()
    test_structured_stock_signals()
    test_unknown_does_not_block_restock_cooldown()
    test_route_integrity()
    test_store_pins_are_geocoded_and_plausible()
    test_pages_share_one_shell_and_have_no_broken_local_links()
    test_repo_stays_organised_and_the_readme_matches_it()
    test_the_name_is_always_PokePing()
    test_home_is_hard_set_and_distances_use_it()
    test_phone_rules_on_every_page()
    test_every_page_script_is_valid_javascript()
    test_30th_complete_coverage()
    test_classify_response()
    test_health_blind_spot_and_recovery()
    test_prune_only_dead_discovered_listings()
    test_main_blocked_retailer_is_silent_but_visible()
    test_main_stops_probing_a_wall_but_keeps_reading_the_rest()
    test_real_pages_classify_correctly()
    test_price_and_msrp_rules()
    test_main_gamestop_trap_never_alerts_but_a_real_listing_does_at_any_price()
    test_live_hits_show_every_in_stock_item_with_its_price()
    test_main_target_placeholder_never_becomes_stock()
    test_main_alert_wording_follows_signal_strength()
    test_every_guide_product_has_a_tcgplayer_mapping()
    test_each_mapping_points_at_the_product_it_claims()
    test_bad_prices_and_unsorted_rows_are_handled()
    test_prices_are_matched_by_product_id_and_stamped()
    test_a_glitchy_jump_is_ignored_and_flagged()
    test_refresh_failure_keeps_prices_and_says_stale()
    test_thin_response_is_stale_not_live()
    test_full_refresh_is_live_with_real_chase_cards()
    test_paging_collects_every_row()
    test_committed_guide_carries_per_product_freshness()
    test_advisor_query_building()
    test_real_retailer_titles_find_their_tcgplayer_product()
    test_fetch_query_retries_once()
    test_advisor_picks_the_right_tcgplayer_product()
    test_advisor_cache_and_failure_behaviour()
    test_price_verdicts()
    test_stock_embed_is_clean_linked_and_within_discord_limits()
    test_embed_ping_rules_and_tones()
    test_embed_survives_hostile_lengths()
    test_post_sends_payload_and_never_raises()
    test_one_listing_is_one_url()
    test_seed_urls_with_tracking_junk_are_requested_and_stored_canonically()
    test_state_merges_url_variants_and_keeps_the_newest_reading()
    test_new_listing_burst_is_capped_with_one_summary()
    test_a_live_hit_clears_fifteen_minutes_after_it_came_into_stock()
    test_live_online_never_shows_an_item_that_is_not_confirmed_in_stock_even_with_a_leftover_start_time()
    test_monitor_tracks_when_a_stay_in_stock_began()
    test_the_watchlist_covers_the_etbs_marco_named()
    test_every_watched_etb_a_store_can_be_read_for_has_a_seed_url()
    test_ultra_premium_collections_are_watched_and_priced()
    test_walmart_alerts_carry_a_real_add_to_cart_link_and_the_links_are_plainly_named()
    test_best_buy_alerts_add_to_cart_with_the_numeric_sku()
    test_the_best_buy_sku_reaches_the_alert_and_is_remembered_in_state()
    test_a_new_best_buy_listing_alert_also_carries_the_add_to_cart_sku()
    test_watchlist_matching_is_by_whole_word_and_regular_boxes_only()
    test_coverage_cells_never_confuse_unreadable_with_out_of_stock()
    test_auto_discovered_products_get_their_own_rows()
    test_watchlist_market_prices_come_from_the_right_tcgplayer_product()
    test_cycle_writes_the_coverage_board_and_survives_its_failure()
    test_gamestop_listing_pids_come_from_the_real_markup()
    test_only_pokemon_etbs_and_upcs_are_wanted()
    test_discovery_finds_a_brand_new_set_once_and_caches_every_id()
    test_main_tracks_a_newly_discovered_etb_and_announces_it_once()
    test_an_alert_needs_two_agreeing_readings()
    test_hot_listings_are_checked_every_cycle_and_the_rest_less_often()
    test_browser_reader_decides_from_the_visible_buy_button_on_real_pages()
    test_browser_stores_are_read_by_the_browser_and_the_rest_over_http()
    test_the_browser_starts_only_when_enabled_and_failure_is_harmless()
    test_the_real_browser_reads_real_pages()
    test_store_hours_flip_to_open_and_closed_live_in_a_real_browser()
    test_the_map_re_reads_the_clock_by_itself()
    test_store_hours_data_is_complete_and_sourced()
    test_page_assets_are_versioned_so_a_browser_never_mixes_releases()
    test_ci_runs_the_end_to_end_test()
    test_the_watcher_never_shares_a_concurrency_group_with_test_runs()
    test_the_deploy_verifier_passes_a_good_site_and_fails_a_broken_one()
    test_browser_stores_are_read_gently()
    test_a_failing_search_engine_is_not_asked_again_for_a_while()
    test_keyword_discovery_stops_at_its_time_budget()
    test_an_item_that_is_in_stock_is_checked_at_the_hot_rhythm()
    test_a_slow_cycle_stops_at_its_budget_and_a_failing_store_is_left_alone()
    test_progress_is_saved_after_each_store()
    test_loop_scheduling()
    test_cycle_refreshes_prices_on_schedule_and_survives_a_price_failure()
    test_the_loop_commits_a_heartbeat_even_when_nothing_changed()
    test_a_failed_commit_attempt_does_not_retry_every_cycle()
    test_loop_commits_on_meaningful_change_only()
    test_signature_ignores_last_seen()
    test_fast_pass_does_not_search_and_rechecks_known_listings()
    test_one_alert_per_stay_in_stock_and_rearming_needs_confirmed_out_of_stock()
    test_rearm_state_rules()
    test_a_marketplace_reseller_is_not_the_store_restocking()
    test_a_reseller_listing_never_alerts_and_clears_a_stale_in_stock_state()
    test_a_new_listing_alert_uses_up_that_stay()
    test_new_listing_announced_once_and_only_when_in_stock()
    test_main_marks_new_listings_and_leaves_legacy_ones_alone()
    print("accuracy, detection, health, route integrity, and 30th coverage tests passed")
