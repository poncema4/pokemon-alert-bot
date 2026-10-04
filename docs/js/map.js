/* Restock Radar: draws the stores, the live online hits and the retailer status lamps. */
(function () {
  const C = window.PokeCommon;
  let HOME = [40.7884, -74.1332]; // town centre until stores.json (and then this browser's saved home) says otherwise
  let homeMarker = null, homeIsCustom = false;
  const COLORS = { target: "#e4352b", walmart: "#2d8cf0", bestbuy: "#4f6bed", gamestop: "#c9ced6", pokemoncenter: "#ffd23f", lgs: "#d99a2b" };
  const $ = (id) => document.getElementById(id);

  const map = L.map("map", { zoomControl: true }).setView(HOME, 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(map);
  function placeHome() {
    if (homeMarker) map.removeLayer(homeMarker);
    homeMarker = L.marker(HOME, { icon: L.divIcon({ className: "", html: '<div class="pin home"></div>', iconSize: [16, 16] }) }).addTo(map).bindTooltip("Home");
    $("home-note").textContent = homeIsCustom ? "Distances are from your saved home (kept only in this browser)." : "Distances are from the town centre. Set your home for exact miles.";
    $("home-clear").hidden = !homeIsCustom;
  }

  /* Home is private: the repo and site are public, so the exact spot is saved only in this browser. */
  function useHome(townDefault) {
    const fromLink = C.parseHomeParam(location.search);
    if (fromLink) { C.saveHome(localStorage, fromLink[0], fromLink[1]); history.replaceState(null, "", location.pathname + location.hash); }
    const h = C.loadHome(localStorage, townDefault);
    HOME = [h.lat, h.lng]; homeIsCustom = h.custom; origin = HOME;
    placeHome();
  }

  async function setHomeFromAddress(text) {
    const note = $("home-note");
    note.textContent = "Looking that up…";
    try {
      const url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=us&q=" + encodeURIComponent(text);
      const hit = (await (await fetch(url)).json())[0];
      if (!hit || !C.saveHome(localStorage, Number(hit.lat), Number(hit.lon))) { note.textContent = "Could not find that address in the North Jersey / NYC area. Try the street and town."; return; }
      useHome(HOME); drawLive(); drawStores(); fitAll();
    } catch (e) { note.textContent = "Address lookup failed. Try again, or use my location."; }
  }

  const TOWN = HOME.slice();
  let stores = [], alerts = [], health = {}, origin = HOME, filter = "all", selected = null;
  const markers = new Map();

  function pinIcon(store, on) {
    return L.divIcon({ className: "", html: '<div class="pin' + (on ? " selected" : "") + '" style="background:' + (COLORS[store.retailer] || COLORS.lgs) + '"></div>', iconSize: [16, 16] });
  }

  function popupHtml(store) {
    const open = C.openNow(store);
    return "<b>" + C.escapeHtml(store.name) + "</b><br>" + C.escapeHtml(store.address) + "<br>" + C.escapeHtml(store.hours || "")
      + (open === null ? "" : '<br><span class="' + (open ? "state-open" : "state-closed") + '">' + (open ? "Open now (approx.)" : "Closed now (approx.)") + "</span>")
      + '<br><a class="btn primary" style="margin-top:8px" target="_blank" rel="noopener" href="' + C.directionsUrl(store) + '">Directions</a>';
  }

  function drawLamps() {
    const now = Date.now();
    $("lamps").innerHTML = C.RETAILERS.map((r) => {
      const s = C.retailerState(health, r, now);
      return '<span class="lamp" data-state="' + s.state + '" title="' + C.escapeHtml(C.LABELS[r] + ": " + s.note) + '"><i></i>' + C.LABELS[r] + " <small>" + C.escapeHtml(s.state === "ok" ? "reading" : s.state) + "</small></span>";
    }).join("");
    const runs = C.RETAILERS.map((r) => health[r] && health[r].last_run).filter(Boolean).sort().pop();
    $("checked").textContent = runs ? "bot checked " + C.ago(runs, now) : "";
  }

  function drawLive() {
    const now = Date.now();
    const live = alerts.filter((a) => C.isLive(a, now));
    if (!live.length) {
      const blind = C.RETAILERS.filter((r) => ["blind", "stale"].indexOf(C.retailerState(health, r, now).state) !== -1).map((r) => C.LABELS[r]);
      $("live").innerHTML = '<div class="empty"><b>Nothing live right now.</b>' + (blind.length ? "<br>The bot cannot read " + C.escapeHtml(blind.join(", ")) + " at the moment, so check those by hand." : "") + "</div>";
      return;
    }
    $("live").innerHTML = live.slice(0, 20).map((a) => {
      const near = C.nearestStore(stores, a.retailer, origin);
      const pickup = near ? '<a class="btn" target="_blank" rel="noopener" href="' + C.directionsUrl(near.store) + '">Nearest ' + C.escapeHtml(C.LABELS[a.retailer]) + " · " + near.miles.toFixed(1) + " mi</a>" : "";
      return '<article class="live-card"><h3>' + C.escapeHtml(a.title) + "</h3><p>" + C.escapeHtml(C.LABELS[a.retailer]) + " · detected " + C.ago(a.detected_at || a.ts, now) + " · stock can change before you click</p>"
        + '<div class="actions"><a class="btn primary" target="_blank" rel="noopener" href="' + C.safeUrl(a.url) + '">Open product</a>' + pickup + "</div></article>";
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
      const open = C.openNow(s);
      const tag = open === null ? "" : '<span class="tag ' + (open ? "open" : "closed") + '">' + (open ? "Open" : "Closed") + "</span>";
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
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((p) => { origin = [p.coords.latitude, p.coords.longitude]; $("home-note").textContent = "Distances are from your current location."; drawLive(); drawStores(); }, () => { $("locate").textContent = "Location blocked"; });
  });
  $("home-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("home-input").value.trim(); if (v) setHomeFromAddress(v); });
  $("home-clear").addEventListener("click", () => { C.clearHome(localStorage); useHome(TOWN); drawLive(); drawStores(); fitAll(); });

  async function getJson(name, fallback) {
    try { return await (await fetch(name + "?ts=" + Date.now())).json(); } catch (e) { return fallback; }
  }

  async function load(first) {
    const [s, a, h] = await Promise.all([getJson("stores.json", { stores: [] }), getJson("alerts.json", []), getJson("health.json", {})]);
    stores = s.stores || []; alerts = a || []; health = h || {};
    if (first && s.home) { TOWN[0] = s.home.lat; TOWN[1] = s.home.lng; useHome(TOWN); }
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
})();
