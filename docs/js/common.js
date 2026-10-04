/* Pure helpers shared by the pages (also loaded by Node for tests). No DOM access here. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PokeCommon = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const RETAILERS = ["target", "walmart", "bestbuy", "gamestop", "pokemoncenter"];
  const LABELS = { target: "Target", walmart: "Walmart", bestbuy: "Best Buy", gamestop: "GameStop", pokemoncenter: "Pokémon Center", lgs: "Shop" };
  const STALE_MINUTES = 20;

  function miles(a, b) {
    const r = 3958.8, p = Math.PI / 180;
    const dLat = (b[0] - a[0]) * p, dLng = (b[1] - a[1]) * p;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * p) * Math.cos(b[0] * p) * Math.sin(dLng / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(h));
  }

  function ago(iso, now) {
    const t = Date.parse(iso);
    if (!iso || Number.isNaN(t)) return "unknown";
    const s = Math.max(0, Math.round(((now || Date.now()) - t) / 1000));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  }

  /* One lamp per retailer: ok (readable), blind (every check blocked), stale (the bot itself has not run lately), unknown. */
  function retailerState(health, retailer, now) {
    if (retailer === "pokemoncenter") return { state: "blind", note: "not readable by bots; use their official alerts" };
    const h = health && health[retailer];
    if (!h || !h.last_run) return { state: "unknown", note: "no data yet" };
    const age = ((now || Date.now()) - Date.parse(h.last_run)) / 60000;
    if (age > STALE_MINUTES) return { state: "stale", note: "last check " + ago(h.last_run, now) };
    if (h.blind_since) return { state: "blind", note: "blocked since " + ago(h.blind_since, now) };
    if (h.checked && !h.readable) return { state: "blind", note: "blocked this run" };
    return { state: "ok", note: "checked " + ago(h.last_run, now) };
  }

  function isLive(alert, now) {
    return !!alert && alert.verified === true && alert.kind === "stock" && !!alert.expires_at
      && Date.parse(alert.expires_at) > (now || Date.now()) && RETAILERS.indexOf(alert.retailer) !== -1;
  }

  function openNow(store, date) {
    if (!store.open || !store.close) return null;
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(date || new Date());
    const hour = Number(parts.find((x) => x.type === "hour").value) % 24;
    const minute = Number(parts.find((x) => x.type === "minute").value);
    const nowMin = hour * 60 + minute;
    const [oh, om] = store.open.split(":").map(Number);
    const [ch, cm] = store.close.split(":").map(Number);
    return nowMin >= oh * 60 + om && nowMin < ch * 60 + cm;
  }

  /* Nearest store of a retailer, so a live online hit can point at the closest place to pick it up. */
  function nearestStore(stores, retailer, from) {
    let best = null;
    stores.forEach((s) => {
      if (s.retailer !== retailer) return;
      const d = miles(from, [s.lat, s.lng]);
      if (!best || d < best.miles) best = { store: s, miles: d };
    });
    return best;
  }

  function directionsUrl(store) {
    return "https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(store.address);
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* Only http(s) links are ever put into an href. */
  function safeUrl(url) {
    return /^https?:\/\//i.test(url || "") ? url : "#";
  }

  /* Home is private: the repo and the site are public, so the exact spot lives only in this browser (localStorage).
     The default in stores.json is just the town. Valid homes are inside the North Jersey / NYC box. */
  const HOME_KEY = "restock-radar-home";
  const BOX = { south: 40.4, north: 41.2, west: -74.5, east: -73.6 };

  function validPoint(lat, lng) {
    return Number.isFinite(lat) && Number.isFinite(lng) && lat > BOX.south && lat < BOX.north && lng > BOX.west && lng < BOX.east;
  }

  /* "?home=40.79,-74.12" -> [lat, lng], or null when missing, malformed or outside the region. */
  function parseHomeParam(search) {
    const m = /[?&]home=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/.exec(search || "");
    if (!m) return null;
    const lat = Number(m[1]), lng = Number(m[2]);
    return validPoint(lat, lng) ? [lat, lng] : null;
  }

  function loadHome(storage, fallback) {
    try {
      const saved = JSON.parse(storage.getItem(HOME_KEY));
      if (saved && validPoint(saved.lat, saved.lng)) return { lat: saved.lat, lng: saved.lng, custom: true };
    } catch (e) { /* no storage (private mode) or bad JSON: use the default */ }
    return { lat: fallback[0], lng: fallback[1], custom: false };
  }

  function saveHome(storage, lat, lng) {
    if (!validPoint(lat, lng)) return false;
    try { storage.setItem(HOME_KEY, JSON.stringify({ lat, lng })); return true; } catch (e) { return false; }
  }

  function clearHome(storage) {
    try { storage.removeItem(HOME_KEY); } catch (e) { /* nothing to clear */ }
  }

  return { HOME_KEY, validPoint, parseHomeParam, loadHome, saveHome, clearHome, RETAILERS, LABELS, STALE_MINUTES, miles, ago, retailerState, isLive, openNow, nearestStore, directionsUrl, escapeHtml, safeUrl };
});
