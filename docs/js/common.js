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

  const STATE_WORDS = { ok: "reading", blind: "can't read", stale: "offline", unknown: "no data" };

  /* The status lamps shown in every page's top bar (one per retailer). Pure: returns HTML. */
  function lampsHtml(health, now) {
    return RETAILERS.map((r) => {
      const s = retailerState(health, r, now);
      return '<span class="lamp" data-state="' + s.state + '" title="' + escapeHtml(LABELS[r] + ": " + COVERAGE_NOTES[r]) + '"><i></i>' + escapeHtml(LABELS[r]) + " <small>" + STATE_WORDS[s.state] + "</small></span>";
    }).join("");
  }

  function isLive(alert, now) {
    return !!alert && alert.verified === true && alert.kind === "stock" && !!alert.expires_at
      && Date.parse(alert.expires_at) > (now || Date.now()) && RETAILERS.indexOf(alert.retailer) !== -1;
  }

  /* ---- Store hours: ONE reading of the weekly schedule text, used by the map, route and ETB pages ---- */
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function toMinutes(text) {
    const m = /^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?$/i.exec(String(text).trim());
    if (!m) return null;
    let h = Number(m[1]) % 12;
    if (m[3].toLowerCase() === "p") h += 12;
    return h * 60 + Number(m[2] || 0);
  }

  function dayIndexes(spec) {
    const name = (t) => DAYS.findIndex((d) => d.toLowerCase() === t.trim().slice(0, 3).toLowerCase());
    const parts = spec.split(/\s*[–—-]\s*/);
    if (parts.length === 2) {
      const a = name(parts[0]), b = name(parts[1]);
      if (a < 0 || b < 0) return null;
      const out = [];
      for (let i = a; ; i = (i + 1) % 7) { out.push(i); if (i === b) break; }   // "Fri–Mon" wraps over the weekend
      return out;
    }
    const list = spec.split(/\s*[,&/]\s*/).map(name);
    return list.length && list.every((i) => i >= 0) ? list : null;
  }

  /* "Mon–Sat 8:00 AM–11:00 PM; Sun closed" -> {days:[Sun..Sat], listed:n}. A day is {open, close} (minutes), "closed",
     or undefined when the text does not mention it. Returns null when the text has no readable schedule at all. */
  function parseSchedule(text) {
    const days = new Array(7).fill(undefined);
    let listed = 0;
    String(text || "").split(";").map((x) => x.trim()).filter(Boolean).forEach((part) => {
      const m = /^([A-Za-z]{3}[A-Za-z]*(?:\s*[–—-]\s*[A-Za-z]{3}[A-Za-z]*|(?:\s*[,&/]\s*[A-Za-z]{3}[A-Za-z]*)*))\s+(.+)$/.exec(part);
      if (!m) return;
      const idx = dayIndexes(m[1]);
      if (!idx) return;
      const rest = m[2].trim();
      let value;
      if (/^closed\b/i.test(rest)) value = "closed";
      else {
        const t = /^(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)\s*[–—-]\s*(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)/i.exec(rest);
        if (!t) return;
        const open = toMinutes(t[1]);
        let close = toMinutes(t[2]);
        if (open === null || close === null) return;
        if (close <= open) close += 1440;   // 12:00 AM or an after-midnight close belongs to the NEXT morning
        value = { open, close };
      }
      idx.forEach((i) => { days[i] = value; });
      listed += idx.length;
    });
    return listed ? { days, listed } : null;
  }

  function easternParts(date) {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(date || new Date());
    const get = (t) => p.find((x) => x.type === t).value;
    return { day: DAYS.indexOf(get("weekday")), minutes: (Number(get("hour")) % 24) * 60 + Number(get("minute")) };
  }

  function clock(min) {
    const m = min % 1440, h = Math.floor(m / 60);
    return ((h % 12) || 12) + (m % 60 ? ":" + String(m % 60).padStart(2, "0") : "") + (h < 12 ? " AM" : " PM");
  }

  function describe(day) {
    return day === "closed" ? "Closed" : day === undefined ? "Not listed" : clock(day.open) + "–" + clock(day.close);
  }

  function nextOpen(sched, now) {
    for (let i = 1; i <= 7; i++) {
      const d = sched.days[(now.day + i) % 7];
      if (d && d !== "closed") return " · opens " + (i === 1 ? "tomorrow" : DAYS[(now.day + i) % 7]) + " " + clock(d.open);
    }
    return "";
  }

  /* {state: "open" | "closed" | "unknown", label, today}. Unknown hours are never shown as open. Uses Eastern time whatever the visitor's clock says. */
  function storeStatus(store, date) {
    const sched = parseSchedule(store && store.hours);
    if (!sched) return { state: "unknown", label: "Hours unknown", today: "Check the store's own site" };
    const now = easternParts(date);
    const today = sched.days[now.day], yesterday = sched.days[(now.day + 6) % 7];
    if (yesterday && yesterday !== "closed" && yesterday.close > 1440 && now.minutes < yesterday.close - 1440) {
      return { state: "open", label: "Open now · until " + clock(yesterday.close), today: describe(today) };   // still open from last night
    }
    if (today === undefined) return { state: "unknown", label: "Hours unknown today", today: "Not listed" };
    if (today === "closed") return { state: "closed", label: "Closed today" + nextOpen(sched, now), today: "Closed" };
    if (now.minutes >= today.open && now.minutes < today.close) return { state: "open", label: "Open now · until " + clock(today.close), today: describe(today) };
    if (now.minutes < today.open) return { state: "closed", label: "Closed · opens " + clock(today.open), today: describe(today) };
    return { state: "closed", label: "Closed now" + nextOpen(sched, now), today: describe(today) };
  }

  /* Seven rows, Monday first, for showing the full week. */
  function weekRows(store) {
    const sched = parseSchedule(store && store.hours);
    return [1, 2, 3, 4, 5, 6, 0].map((i) => ({ day: DAYS[i], text: sched ? describe(sched.days[i]) : "Unknown" }));
  }

  /* true / false / null (unknown). Kept for callers that only need a yes/no. */
  function openNow(store, date) {
    const st = storeStatus(store, date).state;
    return st === "open" ? true : st === "closed" ? false : null;
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

  /* Why each retailer is, or is not, readable by the bot (measured 2026-10-04 from GitHub's runner). */
  const COVERAGE_NOTES = {
    target: "Target shows a placeholder page and hides real stock behind a captcha the bot never tries to pass.",
    walmart: "Walmart sometimes redirects automated visitors to a bot wall; it is read whenever it lets the bot in.",
    bestbuy: "Best Buy is read in a real browser, but its bot defence serves an empty page about half the time, so readings are spotty.",
    gamestop: "GameStop's page includes its own availability flag, so this one is read directly.",
    pokemoncenter: "Pokémon Center blocks automated visitors (403 or a robot check).",
  };

  return { STATE_WORDS, lampsHtml, COVERAGE_NOTES, RETAILERS, LABELS, STALE_MINUTES, miles, ago, retailerState, isLive, openNow, storeStatus, parseSchedule, weekRows, nearestStore, directionsUrl, escapeHtml, safeUrl };
});
