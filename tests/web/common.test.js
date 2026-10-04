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

// private home: stored only in the browser, validated, never trusted blindly
const memory = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; } }; };
const TOWN = [40.7884, -74.1332];
assert.deepStrictEqual(C.parseHomeParam("?home=40.7968,-74.1255"), [40.7968, -74.1255]);
assert.deepStrictEqual(C.parseHomeParam("?x=1&home=40.7968,-74.1255"), [40.7968, -74.1255]);
assert.strictEqual(C.parseHomeParam("?home=40.7968"), null, "needs both numbers");
assert.strictEqual(C.parseHomeParam("?home=abc,def"), null);
assert.strictEqual(C.parseHomeParam("?home=34.05,-118.24"), null, "Los Angeles is outside the region");
assert.strictEqual(C.parseHomeParam(""), null);
let homeStore = memory();
assert.deepStrictEqual(C.loadHome(homeStore, TOWN), { lat: 40.7884, lng: -74.1332, custom: false }, "default is the town");
assert.strictEqual(C.saveHome(homeStore, 40.7968, -74.1255), true);
assert.deepStrictEqual(C.loadHome(homeStore, TOWN), { lat: 40.7968, lng: -74.1255, custom: true });
assert.strictEqual(C.saveHome(homeStore, 10, 10), false, "an out-of-region home is refused");
assert.deepStrictEqual(C.loadHome(homeStore, TOWN).lat, 40.7968, "a refused save keeps the old home");
C.clearHome(homeStore);
assert.strictEqual(C.loadHome(homeStore, TOWN).custom, false);
homeStore.setItem(C.HOME_KEY, "{not json");
assert.strictEqual(C.loadHome(homeStore, TOWN).custom, false, "corrupt storage falls back to the default");
homeStore.setItem(C.HOME_KEY, JSON.stringify({ lat: 1, lng: 2 }));
assert.strictEqual(C.loadHome(homeStore, TOWN).custom, false, "a saved home outside the region is ignored");
const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
assert.strictEqual(C.loadHome(broken, TOWN).custom, false, "blocked storage must not break the page");
assert.strictEqual(C.saveHome(broken, 40.79, -74.12), false);

console.log("web helper tests passed");
