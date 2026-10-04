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

console.log("gmail forwarder tests passed");
