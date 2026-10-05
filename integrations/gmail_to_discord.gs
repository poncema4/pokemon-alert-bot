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
  { name: "Target", key: "target", home: "https://www.target.com/", domains: ["target.com"] },
  { name: "Walmart", key: "walmart", home: "https://www.walmart.com/", domains: ["walmart.com"] },
  { name: "Best Buy", key: "bestbuy", home: "https://www.bestbuy.com/", domains: ["bestbuy.com"] },
  { name: "GameStop", key: "gamestop", home: "https://www.gamestop.com/", domains: ["gamestop.com"] },
  { name: "Pokémon Center", key: "pokemoncenter", home: "https://www.pokemoncenter.com/", domains: ["pokemoncenter.com", "pokemon.com"] },
];
const ALERT_TTL_MS = 15 * 60 * 1000;      // how long an email alert stays on the website's Live online section (the same 15 minutes as the bot's own alerts)
const KEEP_MS = 24 * 3600 * 1000;         // forwarded alerts are remembered this long, then forgotten
const MAX_ALERTS = 30;
const ALERTS_PROPERTY = "ALERTS_JSON";
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

/** "Target <no-reply@e.target.com>" -> that store's record ({name, key, home, domains}), or null when the sender is not one of the five stores. */
function retailerRecord(from) {
  const m = /@([A-Za-z0-9.-]+)/.exec(String(from || ""));
  if (!m) return null;
  const host = m[1].toLowerCase();
  for (const r of RETAILERS) {
    if (r.domains.some((d) => host === d || host.endsWith("." + d))) return r;
  }
  return null;
}

/** The store's name, or null. */
function retailerOf(from) {
  const r = retailerRecord(from);
  return r ? r.name : null;
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
 * msg = { from, subject, plainBody, date (ms) }  ->  { store, subject, link, when } when the email should be forwarded, or null
 * (not from one of the five stores, not about stock, not about a Pokemon product we focus on, or too old).
 */
function classify(msg, now) {
  const store = retailerRecord(msg.from);
  if (!store) return null;
  const subject = plain(msg.subject);
  const head = String(msg.plainBody || "").slice(0, 800);
  if (!RESTOCK_WORDS.test(subject) && !RESTOCK_WORDS.test(head)) return null;
  const queueDrop = store.key === "pokemoncenter" && (QUEUE_WORDS.test(subject) || QUEUE_WORDS.test(head));
  if (!isFocus(subject + " " + String(msg.plainBody || "").slice(0, 1500)) && !queueDrop) return null;   // not a Pokemon product we focus on
  const age = now - Number(msg.date || now);
  if (age > MAX_AGE_MS || age < -60000) return null;
  return { store, subject, link: firstLink(msg.plainBody), when: new Date(Number(msg.date || now)).toISOString() };
}

/** The Discord message for a forwarded email, or null when it should not be forwarded. */
function buildPayload(msg, now) {
  const c = classify(msg, now);
  if (!c) return null;
  return {
    username: "PokePing",
    content: "@everyone",
    allowed_mentions: { parse: ["everyone"] },
    embeds: [{
      title: clip("📧 " + c.store.name + " emailed a back-in-stock alert", 250),
      description: clip("**" + clip(c.subject, 250) + "**" + (c.link ? "\n[Open the store's link](" + c.link + ")" : "\n(no link found in the email: open the app)"), 3500),
      color: 0xF5A623,
      footer: { text: "Forwarded from your email · the store's own alert, not PokePing's check, so it can be late" },
      timestamp: c.when,
    }],
  };
}

/**
 * The same alert in the shape the PokePing website shows in Live online. The website reads these from this script's web app (doGet), so what Discord
 * announced and what the site lists come from one record. No link in the email: the store's home page.
 */
function alertRecord(msg, now, id) {
  const c = classify(msg, now);
  if (!c) return null;
  return {
    id: String(id || msg.id || c.when + c.subject),
    retailer: c.store.key,
    title: clip(c.subject, 200),
    url: c.link || c.store.home,
    detected_at: c.when,
    expires_at: new Date(now + ALERT_TTL_MS).toISOString(),
    signal: "email",
  };
}

/** Remembers a forwarded alert (newest first, no duplicates, nothing older than a day, at most MAX_ALERTS). */
function rememberAlert(store, record, now) {
  let list = [];
  try { list = JSON.parse(store.getProperty(ALERTS_PROPERTY) || "[]"); } catch (e) { list = []; }
  list = [record].concat(list.filter((a) => a && a.id !== record.id && now - Date.parse(a.detected_at) < KEEP_MS)).slice(0, MAX_ALERTS);
  store.setProperty(ALERTS_PROPERTY, JSON.stringify(list));
  return list;
}

/** The alerts that should be on the website right now (not expired). */
function activeAlerts(store, now) {
  let list = [];
  try { list = JSON.parse(store.getProperty(ALERTS_PROPERTY) || "[]"); } catch (e) { list = []; }
  return list.filter((a) => a && Date.parse(a.expires_at) > now);
}

/** Web app endpoint: the PokePing website fetches this to show store-email alerts in Live online. Read-only; it holds alert titles and links, nothing else. */
function doGet() {
  const body = { alerts: activeAlerts(PropertiesService.getScriptProperties(), Date.now()), generated_at: new Date().toISOString() };
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
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
      const msg = { id: message.getId(), from: message.getFrom(), subject: message.getSubject(), plainBody: message.getPlainBody(), date: message.getDate().getTime() };
      const now = Date.now();
      const payload = buildPayload(msg, now);
      if (payload) {
        const response = UrlFetchApp.fetch(hook, { method: "post", contentType: "application/json", payload: JSON.stringify(payload), muteHttpExceptions: true });
        if (response.getResponseCode() >= 300) return;   // Discord refused: leave it unread so the next run retries
        try { rememberAlert(PropertiesService.getScriptProperties(), alertRecord(msg, now, msg.id), now); } catch (e) { Logger.log("could not remember the alert for the website: " + e); }
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

if (typeof module !== "undefined") module.exports = { retailerOf, retailerRecord, firstLink, plain, classify, buildPayload, alertRecord, rememberAlert, activeAlerts, doGet, clip, RETAILERS, normalize, isFocus, FOCUS_TERMS, checkRestockEmails, sendTestToDiscord, installTrigger };
