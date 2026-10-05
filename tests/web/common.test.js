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

// store hours: the WEEKLY schedule decides, in Eastern time. 2026-10-04 is a Sunday; 16:00Z is 12:00 noon EDT.
const SUN_NOON = new Date("2026-10-04T16:00:00Z"), MON_NOON = new Date("2026-10-05T16:00:00Z");
const duck = { hours: "Mon 3:00 PM–9:00 PM; Tue–Fri 12:00 PM–9:00 PM; Sat 11:00 AM–9:00 PM; Sun closed" };
assert.strictEqual(C.storeStatus(duck, SUN_NOON).state, "closed", "TCGDUCKHUNTER is closed on Sunday (the reported bug)");
assert.strictEqual(C.storeStatus(duck, SUN_NOON).label, "Closed today · opens tomorrow 3 PM");
assert.strictEqual(C.storeStatus(duck, MON_NOON).state, "closed", "Monday noon is before the 3 PM opening");
assert.strictEqual(C.storeStatus(duck, new Date("2026-10-05T20:00:00Z")).label, "Open now · until 9 PM", "Monday 4 PM is open");
assert.strictEqual(C.openNow(duck, SUN_NOON), false);
const target = { hours: "Mon–Sat 8:00 AM–11:00 PM; Sun 8:00 AM–10:00 PM" };
assert.strictEqual(C.storeStatus(target, new Date("2026-10-05T02:30:00Z")).state, "closed", "10:30 PM Sunday Eastern is after the Sunday 10 PM close");
assert.strictEqual(C.storeStatus(target, new Date("2026-10-05T01:30:00Z")).state, "open", "9:30 PM Sunday Eastern is open");
assert.strictEqual(C.storeStatus(target, new Date("2026-10-04T09:00:00Z")).state, "closed", "5 AM Eastern is closed");
// the clock is Eastern whatever the visitor's own time zone is: 02:30Z is 10:30 PM Saturday in New Jersey
assert.strictEqual(C.storeStatus({ hours: "Mon–Sun 6:00 AM–11:00 PM" }, new Date("2026-10-04T02:30:00Z")).state, "open");
assert.strictEqual(C.storeStatus({ hours: "Mon–Sun 6:00 AM–11:00 PM" }, new Date("2026-10-04T02:30:00Z")).label, "Open now · until 11 PM");
// a range that wraps the weekend and a close at midnight (belongs to the next morning)
const wrap = C.parseSchedule("Fri–Mon 10:00 AM–12:00 AM; Tue closed");
assert.deepStrictEqual([wrap.days[5] !== undefined, wrap.days[6] !== undefined, wrap.days[0] !== undefined, wrap.days[1] !== undefined, wrap.days[2], wrap.days[3]], [true, true, true, true, "closed", undefined]);
assert.strictEqual(C.storeStatus({ hours: "Sat 10:00 AM–12:00 AM" }, new Date("2026-10-04T03:30:00Z")).state, "open", "11:30 PM Saturday is open until midnight");
assert.strictEqual(C.storeStatus({ hours: "Sat 10:00 AM–2:00 AM" }, new Date("2026-10-04T05:30:00Z")).state, "open", "1:30 AM Sunday is still Saturday's late hours");
// unknown is never "open", and a day the text does not mention is unknown, not closed or open
assert.strictEqual(C.storeStatus({}, new Date()).state, "unknown");
assert.strictEqual(C.storeStatus({ hours: "Check official site; online catalog active" }, new Date()).state, "unknown");
assert.strictEqual(C.openNow({}, new Date()), null, "unknown hours are unknown, not open");
assert.strictEqual(C.storeStatus({ hours: "Tue–Thu 12:00 PM–7:00 PM" }, MON_NOON).state, "unknown", "Monday is not listed");
// the exact opening and closing minute: Walmart 6:00 AM–11:00 PM. 2026-10-05 is a Monday; 09:59Z is 5:59 AM EDT.
const walmart = { hours: "Mon–Sun 6:00 AM–11:00 PM" };
assert.strictEqual(C.storeStatus(walmart, new Date("2026-10-05T09:59:59Z")).state, "closed", "5:59:59 AM is still closed");
assert.strictEqual(C.storeStatus(walmart, new Date("2026-10-05T10:00:00Z")).state, "open", "6:00:00 AM is open");
assert.strictEqual(C.storeStatus(walmart, new Date("2026-10-06T02:59:59Z")).state, "open", "10:59:59 PM is still open");
assert.strictEqual(C.storeStatus(walmart, new Date("2026-10-06T03:00:00Z")).state, "closed", "11:00:00 PM is closed");
// daylight saving: in January Eastern is UTC-5, so 6:00 AM is 11:00Z
assert.strictEqual(C.storeStatus(walmart, new Date("2026-01-12T10:59:00Z")).state, "closed");
assert.strictEqual(C.storeStatus(walmart, new Date("2026-01-12T11:00:00Z")).state, "open");
// the full week for display, Monday first
assert.deepStrictEqual(C.weekRows(duck).map((r) => r.day + " " + r.text), ["Mon 3 PM–9 PM", "Tue 12 PM–9 PM", "Wed 12 PM–9 PM", "Thu 12 PM–9 PM", "Fri 12 PM–9 PM", "Sat 11 AM–9 PM", "Sun Closed"]);

// every real store: no leftover single open/close pair, and a readable weekly schedule unless it is on the explicit "check their site" list
const fs = require("fs");
const real = JSON.parse(fs.readFileSync(__dirname + "/../../docs/stores.json", "utf8")).stores;
const NO_SCHEDULE = [];   // every store now has a real weekly schedule; a new store without one must be added here on purpose
real.forEach((st) => {
  assert.ok(!("open" in st) && !("close" in st), st.id + " must not carry a single open/close pair (it ignores the weekly schedule)");
  const sched = C.parseSchedule(st.hours);
  if (NO_SCHEDULE.includes(st.id)) assert.strictEqual(sched, null, st.id + " has no schedule and must show unknown");
  else assert.ok(sched && sched.listed === 7, st.id + " must list all seven days, got: " + st.hours);
});
const realById = Object.fromEntries(real.map((x) => [x.id, x]));
assert.strictEqual(C.storeStatus(realById["tcgduckhunter"], SUN_NOON).state, "closed");
assert.strictEqual(C.storeStatus(realById["target-paramus"], SUN_NOON).state, "closed", "Target Paramus Sunday is closed in its published schedule");
assert.strictEqual(C.storeStatus(realById["bestbuy-paramus"], SUN_NOON).state, "closed", "Best Buy Paramus is closed on Sundays");
assert.strictEqual(C.storeStatus(realById["target-clifton"], new Date("2026-10-05T02:30:00Z")).state, "open", "Target Clifton is open until 11 PM on Sunday");
assert.strictEqual(C.storeStatus(realById["bodega-hoboken"], MON_NOON).state, "closed", "Bodega Cards is closed on Monday");

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


// ---- alerts from the stores' own emails (the Google script's feed) ----
// only Google's script domains may be asked (or localhost, but only when the page itself is on localhost)
assert.strictEqual(C.isAllowedEmailEndpoint("https://script.google.com/macros/s/AKfy123/exec", "poncema4.github.io"), true);
assert.strictEqual(C.isAllowedEmailEndpoint("https://script.googleusercontent.com/macros/echo?x=1", "poncema4.github.io"), true);
assert.strictEqual(C.isAllowedEmailEndpoint("https://evil.example/exec", "poncema4.github.io"), false, "any other site is refused");
assert.strictEqual(C.isAllowedEmailEndpoint("https://script.google.com.evil.example/exec", "poncema4.github.io"), false, "a look-alike host is refused");
assert.strictEqual(C.isAllowedEmailEndpoint("http://script.google.com/macros/s/x/exec", "poncema4.github.io"), false, "plain http to Google is refused");
assert.strictEqual(C.isAllowedEmailEndpoint("http://127.0.0.1:8123/feed", "poncema4.github.io"), false, "localhost is only allowed when the page is on localhost");
assert.strictEqual(C.isAllowedEmailEndpoint("http://127.0.0.1:8123/feed", "127.0.0.1"), true);
assert.strictEqual(C.isAllowedEmailEndpoint("javascript:alert(1)", "127.0.0.1"), false);
assert.strictEqual(C.isAllowedEmailEndpoint("", "127.0.0.1"), false);
assert.strictEqual(C.isAllowedEmailEndpoint("not a url", "127.0.0.1"), false);

const FNOW = Date.parse("2026-10-04T18:00:00Z");
const fresh = (over) => ({ id: "m1", retailer: "target", title: "Pokémon Pitch Black Elite Trainer Box is back in stock", url: "https://www.target.com/p/-/A-1", detected_at: new Date(FNOW - 60000).toISOString(), expires_at: new Date(FNOW + 14 * 60000).toISOString(), signal: "email", ...over });
const hits = C.emailAlertsToHits([fresh()], FNOW);
assert.strictEqual(hits.length, 1);
assert.deepStrictEqual([hits[0].kind, hits[0].retailer, hits[0].verified, hits[0].signal, hits[0].price], ["stock", "target", true, "email", null]);
assert.strictEqual(C.isLive(hits[0], FNOW), true, "an email alert is shown by the same rule as the bot's own");
assert.strictEqual(C.isLive(hits[0], FNOW + 15 * 60000), false, "and disappears when it expires");
assert.strictEqual(C.emailAlertsToHits([fresh({ expires_at: new Date(FNOW - 1).toISOString() })], FNOW).length, 0, "an expired alert is dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ retailer: "ebay" })], FNOW).length, 0, "an unknown store is dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ retailer: "Target" })], FNOW).length, 0, "the store must be the site's key, not a display name");
assert.strictEqual(C.emailAlertsToHits([fresh({ title: "   " })], FNOW).length, 0, "no title: dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ title: 42 })], FNOW).length, 0, "a non-text title: dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ url: "javascript:alert(1)" })], FNOW).length, 0, "a script link is dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ url: "" })], FNOW).length, 0, "no link: dropped");
assert.strictEqual(C.emailAlertsToHits([fresh({ expires_at: "garbage" })], FNOW).length, 0, "an unreadable expiry is dropped");
assert.deepStrictEqual(C.emailAlertsToHits([null, 5, "x", {}], FNOW), [], "junk entries are ignored");
assert.deepStrictEqual(C.emailAlertsToHits(null, FNOW), [], "a missing list is empty");
assert.deepStrictEqual(C.emailAlertsToHits({ alerts: [] }, FNOW), [], "a wrong shape is empty");
assert.strictEqual(C.emailAlertsToHits([fresh({ title: "x".repeat(900) })], FNOW)[0].title.length, 200, "a huge title is cut");
assert.ok(C.emailAlertsToHits([fresh({ detected_at: "garbage" })], FNOW)[0].detected_at, "an unreadable detection time is replaced by one derived from the expiry");
assert.strictEqual(C.escapeHtml(C.emailAlertsToHits([fresh({ title: "<img src=x onerror=alert(1)>" })], FNOW)[0].title).includes("<img"), false, "titles are escaped before they are shown");

const botLive = { kind: "stock", retailer: "gamestop", title: "GameStop item", url: "https://www.gamestop.com/x", verified: true, detected_at: new Date(FNOW - 5 * 60000).toISOString(), expires_at: new Date(FNOW + 10 * 60000).toISOString() };
const merged = C.mergeLive([botLive], hits);
assert.deepStrictEqual(merged.map((a) => a.retailer), ["target", "gamestop"], "both sources are listed, newest first");
const sameProduct = C.emailAlertsToHits([fresh({ retailer: "gamestop", url: "https://www.gamestop.com/x" })], FNOW);
assert.deepStrictEqual(C.mergeLive([botLive], sameProduct), [botLive], "the same product at the same store is listed once, as the bot's own entry");
assert.deepStrictEqual(C.mergeLive(null, hits).map((a) => a.retailer), ["target"], "email alerts alone still show");
assert.deepStrictEqual(C.mergeLive([botLive], null), [botLive], "bot alerts alone still show");
assert.deepStrictEqual(C.mergeLive(undefined, undefined), []);

console.log("web helper tests passed");
