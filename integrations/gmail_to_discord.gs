/**
 * PokePing email forwarder: turns the stores' OWN back-in-stock emails into Discord alerts the moment they arrive.
 *
 * Why: Target, Walmart and Pokémon Center cannot be read by a bot (they put up captchas / robot checks), but they all send an email when
 * you ask them to ("Notify me", "Get in-stock alert"). This script runs in YOUR Google account, watches Gmail, and posts those emails to
 * the same Discord channel as PokePing, with an @everyone ping. The store decides when its email goes out, so it can be late; it is a
 * second source, not PokePing's own check.
 *
 * Setup is in the README ("Alerts from the stores' own emails"). Paste this whole file into a new Apps Script project.
 */

const RETAILERS = [
  { name: "Target", domains: ["target.com"] },
  { name: "Walmart", domains: ["walmart.com"] },
  { name: "Best Buy", domains: ["bestbuy.com"] },
  { name: "GameStop", domains: ["gamestop.com"] },
  { name: "Pokémon Center", domains: ["pokemoncenter.com", "pokemon.com"] },
];
const RESTOCK_WORDS = /(back in stock|in[- ]stock|is available|now available|available again|available now|restock|just dropped|just landed|waiting room|queue|you'?re in line|your turn)/i;
const NEVER_LINK = /(unsubscribe|preferences|privacy|terms|mailto:|facebook\.com|twitter\.com|x\.com\/|instagram\.com|youtube\.com|tiktok\.com|\.(png|jpe?g|gif|svg)(\?|$)|help\.|support\.)/i;
const MAX_AGE_MS = 24 * 3600 * 1000;   // an old email is not a restock
// The products PokePing focuses on (the set names in config/search_config.json "watchlist", kept in step by a test). An email is only forwarded when it
// is about Pokemon AND names one of these. Written normalised: lower case, no accents, letters and digits only, single spaces.
const FOCUS_TERMS = [
  "30th celebration", "delta reign", "chaos rising", "pitch black", "perfect order", "ascended heroes", "phantasmal flames", "mega evolution",
  "prismatic evolutions", "destined rivals", "black bolt", "white flare", "journey together", "surging sparks", "pokemon 151", "scarlet violet 151",
  "ultra premium collection",
];
const QUEUE_WORDS = /(waiting room|you'?re in line|your turn|queue)/i;   // a Pokemon Center queue email announces a drop even when it names no product
const LABEL = "PokePing";               // the Gmail filter from the README puts this label on store emails

function clip(text, limit) {
  text = String(text == null ? "" : text);
  return text.length <= limit ? text : text.slice(0, limit - 1) + "…";
}

/** "Target <no-reply@e.target.com>" -> the retailer's name, or null when the sender is not one of the five stores. */
function retailerOf(from) {
  const m = /@([A-Za-z0-9.-]+)/.exec(String(from || ""));
  if (!m) return null;
  const host = m[1].toLowerCase();
  for (const r of RETAILERS) {
    if (r.domains.some((d) => host === d || host.endsWith("." + d))) return r.name;
  }
  return null;
}

/** The first link in the email that is not an unsubscribe / social / image link. Only http(s) links are ever returned. */
function firstLink(body) {
  const links = String(body || "").match(/https?:\/\/[^\s<>"')\]]+/g) || [];
  for (const link of links) {
    if (!NEVER_LINK.test(link)) return link.replace(/[.,;]+$/, "");
  }
  return null;
}

/** Lower case, accents removed ("Pokémon" -> "pokemon"), every run of other characters becomes one space. */
function normalize(text) {
  return String(text == null ? "" : text).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** Is this text about Pokemon AND one of the products we focus on? ("151" alone would match a price, so it is only matched as "pokemon 151".) */
function isFocus(text) {
  const hay = " " + normalize(text) + " ";
  return hay.includes(" pokemon ") && FOCUS_TERMS.some((term) => hay.includes(" " + term + " "));
}

/** A subject can contain "@everyone" or line breaks: neutralise them so an email can never ping or break the layout. */
function plain(text) {
  return String(text || "").replace(/\s+/g, " ").replace(/@(everyone|here)/gi, "@​$1").trim();
}

/**
 * msg = { from, subject, plainBody, date (ms) }  ->  a Discord webhook payload, or null when the email should NOT be forwarded
 * (not from one of the five stores, not about stock, or too old).
 */
function buildPayload(msg, now) {
  const retailer = retailerOf(msg.from);
  if (!retailer) return null;
  const subject = plain(msg.subject);
  const head = String(msg.plainBody || "").slice(0, 800);
  if (!RESTOCK_WORDS.test(subject) && !RESTOCK_WORDS.test(head)) return null;
  const queueDrop = retailer === "Pokémon Center" && (QUEUE_WORDS.test(subject) || QUEUE_WORDS.test(head));
  if (!isFocus(subject + " " + String(msg.plainBody || "").slice(0, 1500)) && !queueDrop) return null;   // not a Pokemon product we focus on
  const age = now - Number(msg.date || now);
  if (age > MAX_AGE_MS || age < -60000) return null;
  const link = firstLink(msg.plainBody);
  const when = new Date(Number(msg.date || now)).toISOString();
  return {
    username: "PokePing",
    content: "@everyone",
    allowed_mentions: { parse: ["everyone"] },
    embeds: [{
      title: clip("📧 " + retailer + " emailed a back-in-stock alert", 250),
      description: clip("**" + clip(subject, 250) + "**" + (link ? "\n[Open the store's link](" + link + ")" : "\n(no link found in the email: open the app)"), 3500),
      color: 0xF5A623,
      footer: { text: "Forwarded from your email · the store's own alert, not PokePing's check, so it can be late" },
      timestamp: when,
    }],
  };
}

/** Runs every minute: forwards each unread, labelled store email once, then marks it read. */
function checkRestockEmails() {
  const hook = PropertiesService.getScriptProperties().getProperty("DISCORD_WEBHOOK_URL");
  if (!hook) throw new Error("Add your Discord webhook as the script property DISCORD_WEBHOOK_URL (Project Settings > Script properties).");
  const label = GmailApp.getUserLabelByName(LABEL);
  if (!label) return;
  label.getThreads(0, 30).forEach(function (thread) {
    thread.getMessages().forEach(function (message) {
      if (!message.isUnread()) return;
      const payload = buildPayload({ from: message.getFrom(), subject: message.getSubject(), plainBody: message.getPlainBody(), date: message.getDate().getTime() }, Date.now());
      if (payload) {
        const response = UrlFetchApp.fetch(hook, { method: "post", contentType: "application/json", payload: JSON.stringify(payload), muteHttpExceptions: true });
        if (response.getResponseCode() >= 300) return;   // Discord refused: leave it unread so the next run retries
      }
      message.markRead();
    });
  });
}

/** Run this once from the Apps Script editor to check the Discord connection: it posts a clearly labelled TEST message (no @everyone). */
function sendTestToDiscord() {
  const hook = PropertiesService.getScriptProperties().getProperty("DISCORD_WEBHOOK_URL");
  if (!hook) throw new Error("Add your Discord webhook as the script property DISCORD_WEBHOOK_URL first.");
  const response = UrlFetchApp.fetch(hook, { method: "post", contentType: "application/json", muteHttpExceptions: true,
    payload: JSON.stringify({ username: "PokePing", content: "*(test: no ping)*", allowed_mentions: { parse: [] },
      embeds: [{ title: "🧪 TEST · email forwarder is connected", description: "When Target, Walmart, Best Buy, GameStop or Pokémon Center email you a back-in-stock alert, it will show up here within about a minute.", color: 0xF5A623 }] }) });
  Logger.log("Discord answered HTTP " + response.getResponseCode() + (response.getResponseCode() < 300 ? " (connected)" : " (check the webhook URL)"));
}

/** Run this once from the Apps Script editor to start the every-minute check. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === "checkRestockEmails") ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger("checkRestockEmails").timeBased().everyMinutes(1).create();
}

if (typeof module !== "undefined") module.exports = { retailerOf, firstLink, plain, buildPayload, clip, RETAILERS, normalize, isFocus, FOCUS_TERMS, checkRestockEmails, sendTestToDiscord, installTrigger };
