// Tests for the Gmail -> Discord forwarder (integrations/gmail_to_discord.gs). The Apps Script glue (GmailApp, UrlFetchApp) cannot run here,
// so the logic that decides what is forwarded and how it looks is kept in pure functions and tested. Run: node tests/web/gmail_forwarder.test.js
const assert = require("assert");
const F = require("../../integrations/gmail_to_discord.gs");

const NOW = Date.parse("2026-10-04T18:00:00Z");
const email = (over) => ({ from: "Target <no-reply@e.target.com>", subject: "Good news! Pokémon TCG Elite Trainer Box is back in stock", date: NOW - 60000,
  plainBody: "Hi Marco,\nIt's back in stock: https://www.target.com/p/-/A-1010892076?ref=email\nUnsubscribe: https://target.com/unsubscribe?x=1", ...over });

// which sender is which store
assert.strictEqual(F.retailerOf("Target <no-reply@e.target.com>"), "Target");
assert.strictEqual(F.retailerOf("walmart@em.walmart.com"), "Walmart");
assert.strictEqual(F.retailerOf("Best Buy <BestBuyInfo@emailinfo.bestbuy.com>"), "Best Buy");
assert.strictEqual(F.retailerOf("GameStop <gamestop@e.gamestop.com>"), "GameStop");
assert.strictEqual(F.retailerOf("Pokémon Center <noreply@pokemoncenter.com>"), "Pokémon Center");
assert.strictEqual(F.retailerOf("deals@target.com.evil.example"), null, "a look-alike domain is not Target");
assert.strictEqual(F.retailerOf("someone@gmail.com"), null);
assert.strictEqual(F.retailerOf(""), null);
assert.strictEqual(F.retailerOf("nobody"), null);

// a real restock email becomes one @everyone message with a hyperlink (no raw URL, so Discord shows no giant preview)
const p = F.buildPayload(email(), NOW);
assert.strictEqual(p.content, "@everyone");
assert.deepStrictEqual(p.allowed_mentions, { parse: ["everyone"] });
assert.ok(p.embeds[0].title.includes("Target") && p.embeds[0].title.includes("back-in-stock"));
assert.ok(p.embeds[0].description.includes("[Open the store's link](https://www.target.com/p/-/A-1010892076?ref=email)"), p.embeds[0].description);
assert.ok(!/unsubscribe/i.test(p.embeds[0].description), "the unsubscribe link is never the link");
assert.ok(p.embeds[0].description.includes("Elite Trainer Box is back in stock"));
assert.ok(p.embeds[0].footer.text.includes("not PokePing's check"), "the message says it is the store's own, possibly late, alert");

// every store works
for (const [from, name] of [["walmart@em.walmart.com", "Walmart"], ["BestBuyInfo@emailinfo.bestbuy.com", "Best Buy"], ["gamestop@e.gamestop.com", "GameStop"], ["noreply@pokemoncenter.com", "Pokémon Center"]]) {
  const q = F.buildPayload(email({ from, subject: "Your item is available now" }), NOW);
  assert.ok(q && q.embeds[0].title.includes(name), name);
}
// a queue email from Pokémon Center is a restock-style alert too
assert.ok(F.buildPayload(email({ from: "noreply@pokemoncenter.com", subject: "The waiting room is open", plainBody: "https://www.pokemoncenter.com/" }), NOW));

// NOT forwarded: other senders, marketing that is not about stock, old emails, emails from the future
assert.strictEqual(F.buildPayload(email({ from: "promo@news.example.com" }), NOW), null, "not one of the five stores");
assert.strictEqual(F.buildPayload(email({ subject: "20% off toys this weekend", plainBody: "Shop now https://www.target.com/toys" }), NOW), null, "marketing, not stock");
assert.strictEqual(F.buildPayload(email({ date: NOW - 25 * 3600 * 1000 }), NOW), null, "older than a day");
assert.strictEqual(F.buildPayload(email({ date: NOW + 10 * 60000 }), NOW), null, "dated in the future");
// restock words only in the body still count
assert.ok(F.buildPayload(email({ subject: "An update on your saved item", plainBody: "Great news, it is back in stock! https://www.target.com/p/-/A-1" }), NOW));

// safety: an email can never ping or break the message
const evil = F.buildPayload(email({ subject: "@everyone @here back in stock\nnow" }), NOW);
assert.ok(!/@everyone|@here/.test(evil.embeds[0].description.replace(/@​(everyone|here)/g, "")), "a subject cannot ping");
assert.ok(!evil.embeds[0].description.split("\n")[0].includes("\n"));
assert.strictEqual(evil.content, "@everyone", "the one deliberate ping is the fixed message content");
const longOne = F.buildPayload(email({ subject: "back in stock " + "x".repeat(6000), plainBody: "back in stock " + "y".repeat(9000) + " https://www.target.com/p/-/A-1" }), NOW);
assert.ok(longOne.embeds[0].description.length <= 3500 && longOne.embeds[0].title.length <= 256, "Discord's size limits are respected");
assert.ok(longOne.embeds[0].description.startsWith("**back in stock xxxx") && longOne.embeds[0].description.split("\n")[0].length <= 256, "a huge subject is cut to a sensible length");
assert.ok(longOne.embeds[0].description.split("\n")[0].endsWith("…**"), "and shows that it was cut");
assert.strictEqual(F.clip("abcdef", 4), "abc…");
assert.strictEqual(F.clip("abc", 4), "abc");
assert.strictEqual(F.clip(null, 4), "");
// links: only http(s), never unsubscribe / social / image
assert.strictEqual(F.firstLink("see https://x.example/unsubscribe and https://www.target.com/p/-/A-9"), "https://www.target.com/p/-/A-9");
assert.strictEqual(F.firstLink("https://facebook.com/target https://cdn.example/banner.png"), null);
assert.strictEqual(F.firstLink("no links here"), null);
assert.strictEqual(F.firstLink("go to https://www.walmart.com/ip/1."), "https://www.walmart.com/ip/1", "trailing punctuation is removed");
const noLink = F.buildPayload(email({ plainBody: "It is back in stock, open the app." }), NOW);
assert.ok(noLink.embeds[0].description.includes("no link found"), "an email with no link still alerts");

// ---- the Apps Script glue (what talks to Gmail and Discord), run against mocks that behave like them ----
const HOOK = "https://discord.com/api/webhooks/123/abc";
function world({ props = { DISCORD_WEBHOOK_URL: HOOK }, label = "present", status = 204, messages = [] } = {}) {
  const w = { posts: [], read: [], triggers: [], deleted: [], labelAsked: [] };
  const mk = (m, i) => ({ isUnread: () => m.unread !== false, getFrom: () => m.from, getSubject: () => m.subject, getPlainBody: () => m.body, getDate: () => new Date(m.when || Date.now() - 1000), markRead: () => w.read.push(i) });
  global.PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => props[k] }) };
  global.GmailApp = { getUserLabelByName: (name) => { w.labelAsked.push(name); return label === "present" ? { getThreads: () => [{ getMessages: () => messages.map(mk) }] } : null; } };
  global.UrlFetchApp = { fetch: (url, opts) => { w.posts.push({ url, opts, body: JSON.parse(opts.payload) }); return { getResponseCode: () => status }; } };
  global.Logger = { log: () => {} };
  global.ScriptApp = { getProjectTriggers: () => [{ getHandlerFunction: () => "checkRestockEmails" }, { getHandlerFunction: () => "other" }], deleteTrigger: (t) => w.deleted.push(t.getHandlerFunction()),
    newTrigger: (fn) => ({ timeBased: () => ({ everyMinutes: (n) => ({ create: () => w.triggers.push({ fn, minutes: n }) }) }) }) };
  return w;
}
const restock = { from: "Target <no-reply@e.target.com>", subject: "Your item is back in stock", body: "Open https://www.target.com/p/-/A-1" };
const marketing = { from: "Target <no-reply@e.target.com>", subject: "Weekend deals on toys", body: "Shop https://www.target.com/toys" };
const stranger = { from: "x@example.com", subject: "back in stock", body: "https://example.com" };

let w = world({ messages: [restock] });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 1, "a store restock email is posted once");
assert.strictEqual(w.posts[0].url, HOOK, "to the webhook from the script property");
assert.strictEqual(w.posts[0].opts.method, "post");
assert.strictEqual(w.posts[0].opts.contentType, "application/json");
assert.strictEqual(w.posts[0].body.content, "@everyone");
assert.deepStrictEqual(w.read, [0], "and then marked read so it is never sent twice");
assert.deepStrictEqual(w.labelAsked, ["PokePing"], "it reads the PokePing label");

w = world({ messages: [{ ...restock, unread: false }] });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 0, "an email already read is not forwarded");

w = world({ messages: [marketing, stranger] });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 0, "marketing mail and other senders are not forwarded");
assert.deepStrictEqual(w.read, [0, 1], "but they are marked read so they are not looked at again");

w = world({ messages: [restock], status: 500 });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 1);
assert.deepStrictEqual(w.read, [], "when Discord refuses the email stays unread and is retried on the next run");
w = world({ messages: [restock], status: 429 });
F.checkRestockEmails();
assert.deepStrictEqual(w.read, [], "rate limited: retried later");

w = world({ messages: [restock, { ...restock, subject: "Another item is available now" }] });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 2);
assert.deepStrictEqual(w.read, [0, 1], "each email is posted once");

w = world({ label: "missing", messages: [restock] });
F.checkRestockEmails();
assert.strictEqual(w.posts.length, 0, "no PokePing label yet: nothing happens, no error");

assert.throws(() => { world({ props: {}, messages: [restock] }); F.checkRestockEmails(); }, /DISCORD_WEBHOOK_URL/, "a missing webhook is a clear error, not a silent failure");

w = world();
F.sendTestToDiscord();
assert.strictEqual(w.posts.length, 1);
assert.notStrictEqual(w.posts[0].body.content, "@everyone", "the test message never pings");
assert.deepStrictEqual(w.posts[0].body.allowed_mentions, { parse: [] });
assert.ok(JSON.stringify(w.posts[0].body).includes("TEST"), "and is clearly labelled TEST");
assert.throws(() => { world({ props: {} }); F.sendTestToDiscord(); }, /DISCORD_WEBHOOK_URL/);

w = world();
F.installTrigger();
assert.deepStrictEqual(w.deleted, ["checkRestockEmails"], "an old trigger of ours is replaced, never duplicated, and other triggers are left alone");
assert.deepStrictEqual(w.triggers, [{ fn: "checkRestockEmails", minutes: 1 }], "one trigger, every minute");

console.log("gmail forwarder tests passed");
