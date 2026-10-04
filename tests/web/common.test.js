// Unit tests for the pure page helpers (docs/js/common.js). Run: node tests/web/common.test.js
const assert = require("assert");
const C = require("../../docs/js/common.js");

const NOW = Date.parse("2026-10-04T16:00:00Z");
const minutesAgo = (m) => new Date(NOW - m * 60000).toISOString();

// miles: Kearny Target to Clifton Target is about 4.5 miles; same point is 0
assert.strictEqual(C.miles([40.76, -74.158], [40.76, -74.158]), 0);
const d = C.miles([40.760181, -74.158492], [40.824552, -74.135134]);
assert.ok(d > 4.2 && d < 4.8, "distance " + d);

// ago
assert.strictEqual(C.ago(minutesAgo(0), NOW), "just now");
assert.strictEqual(C.ago(minutesAgo(5), NOW), "5 min ago");
assert.strictEqual(C.ago(minutesAgo(180), NOW), "3 h ago");
assert.strictEqual(C.ago(minutesAgo(3000), NOW), "2 d ago");
assert.strictEqual(C.ago("not a date", NOW), "unknown");
assert.strictEqual(C.ago(null, NOW), "unknown");

// retailer lamps: reading, blind, stale and unknown are four different answers
const health = {
  target: { last_run: minutesAgo(2), checked: 3, readable: 3 },
  walmart: { last_run: minutesAgo(2), checked: 5, readable: 0, blind_since: minutesAgo(1500) },
  bestbuy: { last_run: minutesAgo(90), checked: 2, readable: 2 },
  gamestop: { last_run: minutesAgo(1), checked: 2, readable: 0 },
};
assert.strictEqual(C.retailerState(health, "target", NOW).state, "ok");
assert.strictEqual(C.retailerState(health, "walmart", NOW).state, "blind");
assert.strictEqual(C.retailerState(health, "bestbuy", NOW).state, "stale", "a bot that has not run lately is stale, not fine");
assert.strictEqual(C.retailerState(health, "gamestop", NOW).state, "blind", "a run that read nothing is blind even before 24 h");
assert.strictEqual(C.retailerState({}, "target", NOW).state, "unknown");
assert.strictEqual(C.retailerState(health, "pokemoncenter", NOW).state, "blind", "Pokémon Center cannot be read by bots");

// live hits: verified stock alerts that have not expired, from a known retailer
const live = { verified: true, kind: "stock", retailer: "target", expires_at: new Date(NOW + 60000).toISOString() };
assert.strictEqual(C.isLive(live, NOW), true);
assert.strictEqual(C.isLive({ ...live, verified: false }, NOW), false);
assert.strictEqual(C.isLive({ ...live, kind: "new" }, NOW), false);
assert.strictEqual(C.isLive({ ...live, expires_at: new Date(NOW - 1).toISOString() }, NOW), false);
assert.strictEqual(C.isLive({ ...live, retailer: "ebay" }, NOW), false);
assert.strictEqual(C.isLive(null, NOW), false);

// open now: 8:00-23:00 Eastern. 2026-10-04 16:00Z is 12:00 EDT.
const store = { open: "08:00", close: "23:00" };
assert.strictEqual(C.openNow(store, new Date("2026-10-04T16:00:00Z")), true);
assert.strictEqual(C.openNow(store, new Date("2026-10-04T09:00:00Z")), false, "5 AM Eastern is closed");
assert.strictEqual(C.openNow(store, new Date("2026-10-05T03:30:00Z")), false, "11:30 PM Eastern is closed");
assert.strictEqual(C.openNow({}, new Date()), null, "unknown hours are unknown, not open");

// nearest store of a retailer
const stores = [
  { id: "a", retailer: "target", lat: 40.76, lng: -74.158, address: "A" },
  { id: "b", retailer: "target", lat: 40.915, lng: -74.056, address: "B" },
  { id: "c", retailer: "walmart", lat: 40.7884, lng: -74.1332, address: "C" },
];
assert.strictEqual(C.nearestStore(stores, "target", [40.7884, -74.1332]).store.id, "a");
assert.strictEqual(C.nearestStore(stores, "bestbuy", [40.7884, -74.1332]), null);

// safety: nothing from the data is ever put in a page unescaped, and only http(s) links are used
assert.strictEqual(C.escapeHtml('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
assert.strictEqual(C.safeUrl("javascript:alert(1)"), "#");
assert.strictEqual(C.safeUrl("https://www.target.com/p/-/A-1"), "https://www.target.com/p/-/A-1");
assert.strictEqual(C.directionsUrl({ address: "200 Passaic Ave, Kearny" }).includes("destination=200%20Passaic%20Ave%2C%20Kearny"), true);

// coverage notes explain every retailer the page shows a lamp for
C.RETAILERS.forEach((r) => assert.ok(C.COVERAGE_NOTES[r] && C.COVERAGE_NOTES[r].length > 20, "missing coverage note for " + r));

// lamps: plain words, one per retailer, nothing alarming like "blind"
const lamps = C.lampsHtml(health, NOW);
assert.strictEqual((lamps.match(/class="lamp"/g) || []).length, C.RETAILERS.length);
assert.ok(lamps.includes("<small>reading</small>") && lamps.includes("<small>can't read</small>") && lamps.includes("<small>offline</small>"));
assert.ok(!/blind/i.test(lamps.replace(/<[^>]*>/g, " ")), "the word blind must not appear as visible text in the status lamps");
assert.ok(lamps.includes('data-state="ok"') && lamps.includes('title="Target: '), "each lamp explains itself on hover");
assert.deepStrictEqual(Object.keys(C.STATE_WORDS).sort(), ["blind", "ok", "stale", "unknown"]);

console.log("web helper tests passed");
