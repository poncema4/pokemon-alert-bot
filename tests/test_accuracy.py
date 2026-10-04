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
import monitor_loop
import advisor
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


PAGES = {"index.html": ("PokePing · Map", "Map"), "route.html": ("PokePing · Route", "Route"), "30th.html": ("PokePing · 30th guide", "30th guide")}


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


def _run_main(tmp, config, state, answers, discovered=(), discover=True):
    monitor.STATE_FILE, monitor.ALERTS_FILE, monitor.HEALTH_FILE, monitor.MARKET_FILE = tmp / "state.json", tmp / "alerts.json", tmp / "health.json", tmp / "market.json"
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
        monitor.main(discover=discover)
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
    assert len(commits) == 1, "nothing meaningful changed, so only the final commit attempt runs"


def test_cycle_refreshes_prices_on_schedule_and_survives_a_price_failure():
    import notify_new_listings as nn
    import refresh_live_hits as rl
    order = []
    real = (monitor.main, rl.main, nn.main, prices_mod.main)
    monitor.main = lambda discover=True: order.append(("stock", discover))
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
        monitor.main, rl.main, nn.main, prices_mod.main = real


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


def test_new_listing_announced_once_and_only_when_in_stock():
    import tempfile
    sent = []
    real = (notify_new_listings.send_card, advisor.fetch_query)
    notify_new_listings.send_card = lambda card: sent.append(card)
    advisor.fetch_query = _fake_tcg_search
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": "2026-10-04T12:00:00+00:00"}
    state = {
        "schema_version": 4,
        "target::https://www.target.com/p/-/A-1": {**base, "in_stock": True, "new_announced": False, "signal": "structured"},
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
    assert notify.stock_embed(sent[0])["embeds"][0]["title"].startswith("🆕")
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
    assert monitor.msrp_for(config, "https://www.target.com/p/-/pokemon-booster-bundle/A-1") == 26.94
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
        assert entry["in_stock"] is True and entry["price"] == 84.99 and entry["msrp"] == 49.99
        assert sent[0]["market"]["market"] == 75.58 and sent[0]["verdict"]["label"] == "ABOVE MARKET", "the alert carries the live TCGplayer comparison"
        assert "pitch-black-etb" in json.loads((tmp / "market.json").read_text()), "and the price is cached for next time"
        _, sent = _run_main(tmp, _gs_config(GS_PITCH), state, {"gamestop.com": lambda u: _Resp(200, u, _page("gamestop_pitch_black_etb_available.html"))})
        assert sent == [], "no repeat alert while it stays in stock"


def test_live_hits_show_every_in_stock_item_with_its_price():
    import tempfile
    now = datetime.now(timezone.utc).isoformat()
    base = {"pokemon": True, "title": "Pokemon ETB", "last_seen": now, "in_stock": True, "signal": "page"}
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
    assert "[Open product](" + PB_URL + ")" in values["Links"] and "[Map](" in values["Links"] and "tcgplayer.com/product/692947" in values["Links"]
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
    test_loop_scheduling()
    test_cycle_refreshes_prices_on_schedule_and_survives_a_price_failure()
    test_loop_commits_on_meaningful_change_only()
    test_signature_ignores_last_seen()
    test_fast_pass_does_not_search_and_rechecks_known_listings()
    test_new_listing_announced_once_and_only_when_in_stock()
    test_main_marks_new_listings_and_leaves_legacy_ones_alone()
    print("accuracy, detection, health, route integrity, and 30th coverage tests passed")
