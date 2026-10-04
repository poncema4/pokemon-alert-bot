/* PokePing map: stores, live online hits and the retailer status lamps. */
(function () {
  const C = window.PokeCommon;
  const COLORS = { target: "#e4352b", walmart: "#2d8cf0", bestbuy: "#4f6bed", gamestop: "#c9ced6", pokemoncenter: "#ffd23f", lgs: "#d99a2b" };
  const $ = (id) => document.getElementById(id);

  let home = [40.797211, -74.125219]; // replaced by stores.json -> home as soon as it loads
  let origin = home, usingGps = false;
  let stores = [], alerts = [], health = {}, filter = "all", selected = null, homeMarker = null;
  const markers = new Map();

  const map = L.map("map", { zoomControl: true }).setView(home, 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(map);

  function placeHome() {
    if (homeMarker) map.removeLayer(homeMarker);
    homeMarker = L.marker(home, { icon: L.divIcon({ className: "", html: '<div class="pin home"></div>', iconSize: [16, 16] }) }).addTo(map).bindTooltip("Home");
    $("origin-note").textContent = usingGps ? "Distances are from your current location." : "Distances are from home.";
  }

  function pinIcon(store, on) {
    return L.divIcon({ className: "", html: '<div class="pin' + (on ? " selected" : "") + '" style="background:' + (COLORS[store.retailer] || COLORS.lgs) + '"></div>', iconSize: [16, 16] });
  }

  function tagHtml(st) {
    return '<span class="tag ' + st.state + '">' + C.escapeHtml(st.state === "open" ? "Open" : st.state === "closed" ? "Closed" : "Hours?") + "</span>";
  }

  /* Re-reads the clock: flips the Open/Closed tags and popups in place (no rebuild, so an open popup, the scroll position and the selection stay). */
  function refreshHours() {
    stores.forEach((s) => {
      const card = document.querySelector('.store[data-id="' + s.id + '"] .tag');
      if (card) card.outerHTML = tagHtml(C.storeStatus(s));
      const m = markers.get(s.id);
      if (m) m.setPopupContent(popupHtml(s));
    });
  }

  function popupHtml(store) {
    const st = C.storeStatus(store);
    return "<b>" + C.escapeHtml(store.name) + "</b><br>" + C.escapeHtml(store.address) + "<br>" + C.escapeHtml(store.hours || "")
      + '<br><span class="' + (st.state === "open" ? "state-open" : st.state === "closed" ? "state-closed" : "state-unknown") + '">' + C.escapeHtml(st.label) + "</span>"
      + '<br><a class="btn primary" style="margin-top:8px" target="_blank" rel="noopener" href="' + C.directionsUrl(store) + '">Directions</a>';
  }

  function drawLamps() {
    const now = Date.now();
    $("lamps").innerHTML = C.lampsHtml(health, now);
    $("coverage").innerHTML = C.RETAILERS.map((r) => {
      const s = C.retailerState(health, r, now);
      return '<li data-state="' + s.state + '"><b>' + C.escapeHtml(C.LABELS[r]) + "</b> " + C.STATE_WORDS[s.state] + " · " + C.escapeHtml(C.COVERAGE_NOTES[r]) + "</li>";
    }).join("");
    const runs = C.RETAILERS.map((r) => health[r] && health[r].last_run).filter(Boolean).sort().pop();
    $("checked").textContent = runs ? "checked " + C.ago(runs, now) : "";
  }

  /* One compact row per live hit: same footprint as a store row. */
  function drawLive() {
    const now = Date.now();
    const live = alerts.filter((a) => C.isLive(a, now));
    if (!live.length) {
      const blind = C.RETAILERS.filter((r) => ["blind", "stale"].indexOf(C.retailerState(health, r, now).state) !== -1).map((r) => C.LABELS[r]);
      $("live").innerHTML = '<div class="empty"><b>Nothing live right now.</b>' + (blind.length ? "<br>Not readable: " + C.escapeHtml(blind.join(", ")) + ". Check those by hand." : "") + "</div>";
      return;
    }
    $("live").innerHTML = live.slice(0, 20).map((a) => {
      const near = C.nearestStore(stores, a.retailer, origin);
      const price = a.price ? "$" + Number(a.price).toFixed(2) : "";
      return '<article class="hit"><div class="row"><a class="name" target="_blank" rel="noopener" href="' + C.safeUrl(a.url) + '"><i class="dot-live"></i>' + C.escapeHtml(a.title) + '</a><span class="dist">' + price + "</span></div>"
        + '<div class="addr">' + C.escapeHtml(C.LABELS[a.retailer]) + " · " + C.ago(a.detected_at || a.ts, now) + (a.signal === "text" ? " · unconfirmed" : "")
        + (near ? ' · <a class="near" target="_blank" rel="noopener" href="' + C.directionsUrl(near.store) + '">nearest ' + near.miles.toFixed(1) + " mi</a>" : "") + "</div></article>";
    }).join("");
  }

  function visibleStores() {
    return stores.filter((s) => filter === "all" || s.retailer === filter)
      .map((s) => ({ s, d: C.miles(origin, [s.lat, s.lng]) })).sort((a, b) => a.d - b.d);
  }

  function drawChips() {
    const kinds = ["all"].concat(C.RETAILERS.filter((r) => stores.some((s) => s.retailer === r)), stores.some((s) => s.retailer === "lgs") ? ["lgs"] : []);
    $("chips").innerHTML = kinds.map((k) => '<button class="chip" type="button" data-k="' + k + '" aria-pressed="' + (k === filter) + '">' + (k === "all" ? "All" : C.LABELS[k]) + "</button>").join("");
  }

  function drawStores() {
    const rows = visibleStores();
    markers.forEach((m) => map.removeLayer(m));
    markers.clear();
    $("stores").innerHTML = rows.map(({ s, d }) => {
      const tag = tagHtml(C.storeStatus(s));
      return '<button class="store" type="button" data-id="' + C.escapeHtml(s.id) + '" aria-current="' + (s.id === selected) + '"><div class="row"><span class="name">' + C.escapeHtml(s.name) + '</span><span class="dist">' + d.toFixed(1) + ' mi</span></div><div class="addr">' + tag + C.escapeHtml(s.address) + '</div><div class="hours">' + C.escapeHtml(s.hours || "") + "</div></button>";
    }).join("");
    rows.forEach(({ s }) => {
      const m = L.marker([s.lat, s.lng], { icon: pinIcon(s, s.id === selected), title: s.name, keyboard: false }).addTo(map).bindPopup(popupHtml(s));
      m.on("click", () => select(s.id, false));
      markers.set(s.id, m);
    });
  }

  /* Clicking a card (or a pin) zooms to that store, opens its popup and highlights both. */
  function select(id, fly) {
    const store = stores.find((s) => s.id === id);
    if (!store) return;
    selected = id;
    markers.forEach((m, key) => m.setIcon(pinIcon(stores.find((s) => s.id === key), key === id)));
    document.querySelectorAll(".store").forEach((el) => {
      el.setAttribute("aria-current", String(el.dataset.id === id));
      if (el.dataset.id === id) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    if (fly) map.flyTo([store.lat, store.lng], 17, { duration: 0.8 });
    const m = markers.get(id);
    if (m) setTimeout(() => m.openPopup(), fly ? 850 : 0);
    history.replaceState(null, "", "#" + id);
  }

  function fitAll() {
    // Fit the closest few stores, not every store: one far-away shop would otherwise zoom the whole map out.
    const pts = visibleStores().slice(0, 8).map(({ s }) => [s.lat, s.lng]).concat([origin]);
    if (pts.length > 1) map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
  }

  $("stores").addEventListener("click", (e) => { const b = e.target.closest(".store"); if (b) select(b.dataset.id, true); });
  $("chips").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (!b) return; filter = b.dataset.k; drawChips(); drawStores(); fitAll(); });
  $("locate").addEventListener("click", () => {
    if (usingGps) { usingGps = false; origin = home; $("locate").textContent = "Use my location"; placeHome(); drawLive(); drawStores(); fitAll(); return; }
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((p) => { usingGps = true; origin = [p.coords.latitude, p.coords.longitude]; $("locate").textContent = "Back to home"; placeHome(); drawLive(); drawStores(); fitAll(); }, () => { $("locate").textContent = "Location blocked"; });
  });

  async function getJson(name, fallback) {
    try { return await (await fetch(name + "?ts=" + Date.now())).json(); } catch (e) { return fallback; }
  }

  async function load(first) {
    const [s, a, h] = await Promise.all([getJson("stores.json", { stores: [] }), getJson("alerts.json", []), getJson("health.json", {})]);
    stores = s.stores || []; alerts = a || []; health = h || {};
    if (first && s.home) { home = [s.home.lat, s.home.lng]; origin = home; placeHome(); }
    drawLamps(); drawLive(); drawChips(); drawStores();
    if (first) {
      fitAll();
      const wanted = decodeURIComponent(location.hash.slice(1));
      if (wanted) select(wanted, true);
    }
  }

  placeHome();
  load(true);
  setInterval(() => load(false), 30000);
  setInterval(refreshHours, 15000);   // a store flips to Open / Closed within 15 s of its opening or closing time
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshHours(); });   // a sleeping laptop or background tab catches up at once
})();
