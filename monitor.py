"""Pokémon TCG stock/deal monitor.

Online availability is independent from the local-store radius. The four core
retailers are Target, Walmart, Best Buy and GameStop; niche shops can be monitored
through explicit product URLs without being mistaken for one of those retailers.
"""
from __future__ import annotations
import html, json, math, os, re, sys, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin
import requests
from notify import alert
ROOT=Path(__file__).parent; STATE_FILE=ROOT/"state.json"; CONFIG_FILE=ROOT/"search_config.json"; STORES_FILE=ROOT/"docs/stores.json"; ALERTS_FILE=ROOT/"docs/alerts.json"
SEARCH_URLS={"target":"https://www.target.com/s?searchTerm={q}","walmart":"https://www.walmart.com/search?q={q}","bestbuy":"https://www.bestbuy.com/site/searchpage.jsp?st={q}","gamestop":"https://www.gamestop.com/search/?q={q}&lang=default"}
BASE_URLS={"target":"https://www.target.com","walmart":"https://www.walmart.com","bestbuy":"https://www.bestbuy.com","gamestop":"https://www.gamestop.com"}; DOMAINS={"target":"target.com","walmart":"walmart.com","bestbuy":"bestbuy.com","gamestop":"gamestop.com"}
IN_STOCK_HINTS=("add to cart","add to bag","add to basket","ship it","shipping available","available for shipping","pickup today","pick up today","available for pickup","in stock","low stock")
OUT_OF_STOCK_HINTS=("out of stock","sold out","currently unavailable","pre-order closed")
POKEMON_WORDS=("pokemon","pokémon","etb","elite trainer","booster","trading card","tcg","151","prismatic evolutions","destined rivals","phantasmal flames","white flare","black bolt","ascended heroes","perfect order","pitch black","30th celebration","30th anniversary")
SLICKDEALS_RSS="https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"; REDDIT_DEALS="https://www.reddit.com/r/PokemonTCGDeals/new.json?limit=15"
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"en-US,en;q=0.9"}

def load_json(path,default):
    try:return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as exc: print(f"JSON load failed {path}: {exc}"); return default

def save_json(path,data): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(data,indent=2),encoding="utf-8")
def is_pokemon(text): return any(word in text.lower() for word in POKEMON_WORDS)

def clean_result_url(url):
    url=html.unescape(url)
    if url.startswith("//"):url="https:"+url
    for param in ("uddg","url"):
        m=re.search(rf"[?&]{param}=([^&]+)",url)
        if m:
            candidate=unquote(m.group(1))
            if candidate.startswith("http"):return candidate
    return url

def retailer_url_is_valid(retailer,url):
    low=url.lower(); checks={"target":"/p/","walmart":"/ip/","bestbuy":("/site/","/product/"),"gamestop":("/products/","/pokemon","/trading-card")}; check=checks[retailer]
    return any(x in low for x in check) if isinstance(check,tuple) else check in low

def extract_retailer_urls(retailer,text):
    candidates=re.findall(r'href=[\"\']([^\"\']+)',text,flags=re.I)+re.findall(r'https?://[^\"\'<>\\ ]+',text)
    patterns={"walmart":r"/ip/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+","target":r"/p/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+","bestbuy":r"/(?:site|product)/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+","gamestop":r"/(?:products|pokemon|trading-card)/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+"}
    candidates += [BASE_URLS[retailer]+x for x in re.findall(patterns[retailer],text)]
    links=[]; seen=set()
    for href in candidates:
        full=clean_result_url(urljoin(BASE_URLS[retailer],href)).split('"')[0].split("'")[0]
        if retailer_url_is_valid(retailer,full) and full not in seen: seen.add(full); links.append(full)
    return links[:20]

def fallback_search(http,retailer,keyword):
    query=f'site:{DOMAINS[retailer]} "{keyword}"'
    for engine,endpoint in (("DuckDuckGo",f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),("Bing",f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US")):
        try:
            response=http.get(endpoint,timeout=7); print(f"  fallback {engine} -> HTTP {response.status_code} ({len(response.text)} bytes)")
            if response.status_code<400:
                links=extract_retailer_urls(retailer,response.text)
                if links:return links[:8]
        except Exception as exc: print(f"  fallback {engine} failed: {exc}")
    return []

def discover_products(http,retailer,keyword,timeout):
    try:
        response=http.get(SEARCH_URLS[retailer].format(q=quote_plus(keyword)),timeout=timeout); print(f"  search {retailer} '{keyword}' -> HTTP {response.status_code} ({len(response.text)} bytes)")
        if response.status_code<400:
            links=extract_retailer_urls(retailer,response.text)
            if links:return links
    except Exception as exc: print(f"  direct search failed {retailer} / {keyword}: {exc}")
    print(f"  no usable direct links from {retailer}; using public-search fallback"); return fallback_search(http,retailer,keyword)

def check_product_page(http,url,timeout):
    try:
        response=http.get(url,timeout=timeout,allow_redirects=True)
        if response.status_code>=400: print(f"  product page HTTP {response.status_code}: {url}"); return None
        text=response.text.lower()
    except Exception as exc: print(f"  product check unavailable: {exc}"); return None
    if any(h in text for h in IN_STOCK_HINTS):return True
    if any(h in text for h in OUT_OF_STOCK_HINTS):return False
    return None

def clean_state(state):
    cleaned={"schema_version":3}
    for key,value in state.items():
        if key=="schema_version" or not isinstance(value,dict):continue
        if key.startswith(("slickdeals::","reddit::")):
            title=value.get("title","")
            if value.get("pokemon") is True and is_pokemon(title+" "+key):cleaned[key]=value
            continue
        if "::" not in key:continue
        source,url=key.split("::",1)
        if source in SEARCH_URLS or source.startswith("niche:"):
            if value.get("pokemon") is True or is_pokemon(url+" "+value.get("title","")):cleaned[key]=value
    return cleaned

def recently_alerted(entry,now,hours):
    if not entry or not entry.get("last_alert"):return False
    try:return now-datetime.fromisoformat(entry["last_alert"].replace("Z","+00:00"))<timedelta(hours=hours)
    except Exception:return False

def haversine_miles(a_lat,a_lng,b_lat,b_lng):
    r=3958.7613;p1=math.radians(a_lat);p2=math.radians(b_lat);dp=math.radians(b_lat-a_lat);dl=math.radians(b_lng-a_lng);x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2;return 2*r*math.asin(math.sqrt(x))

def nearby_stores(stores,retailer,home,radius,limit=5):
    matches=[]
    for store in stores:
        if store.get("retailer")!=retailer or not store.get("monitored",False):continue
        if home is None:matches.append((999999,store));continue
        d=haversine_miles(home[0],home[1],store["lat"],store["lng"])
        if d<=radius:matches.append((d,store))
    matches.sort(key=lambda x:x[0]);return [s for _,s in matches[:limit]]

def maps_link(store):return "https://www.google.com/maps/search/?api=1&query="+quote_plus(store.get("maps_query") or f"{store['name']} {store['address']}")

def send_stock_alert(retailer,title,url,pins,map_url,ping,verified):
    headline=f"IN STOCK — {retailer.title()}" if verified is True else f"POSSIBLE POKÉMON HIT — {retailer.title()}"; note="Online product page is showing an availability/cart signal." if verified is True else "A matching product URL was found, but stock could not be verified. Open it immediately."; lines=[title,note,"Online availability is checked independently of the local-store radius."]
    if pins:lines.append("Nearby stores:");lines.extend(f"- {p['name']}: {maps_link(p)}" for p in pins)
    else:lines.append("No monitored local store is within the configured radius; this does not suppress an online alert.")
    if map_url:lines.append(f"Map: {map_url}")
    alert(headline,"\n".join(lines),url,ping=ping)

def record_alert(alerts,kind,retailer,title,url,pins,verified):
    now=datetime.now(timezone.utc);alerts.insert(0,{"ts":now.isoformat(),"expires_at":(now+timedelta(minutes=30)).isoformat(),"kind":kind,"retailer":retailer,"title":title,"url":url,"verified":verified,"online":True,"stores":[{"id":s["id"],"name":s["name"],"lat":s["lat"],"lng":s["lng"]} for s in pins]})

def feed_items(http,url):
    try:
        r=http.get(url,timeout=8)
        if r.status_code>=400:return []
        root=ET.fromstring(r.text)
    except Exception as exc:print(f"  feed failed: {exc}");return []
    out=[]
    for item in root.findall(".//item"):
        title=(item.findtext("title") or "").strip();link=(item.findtext("link") or "").strip();body=re.sub("<[^>]+>"," ",(item.findtext("description") or "")).strip()
        if title and link:out.append({"title":title,"url":link,"body":body[:600]})
    return out

def reddit_items(http):
    try:
        r=http.get(REDDIT_DEALS,timeout=8,headers={**HEADERS,"User-Agent":"pokemon-alert-bot/7.0"})
        if r.status_code>=400:return []
        return [{"title":x.get("data",{}).get("title","") ,"url":x.get("data",{}).get("url","") ,"body":x.get("data",{}).get("selftext","")} for x in r.json().get("data",{}).get("children",[]) if x.get("data",{}).get("title")]
    except Exception as exc:print(f"  reddit failed: {exc}");return []

def main():
    config=load_json(CONFIG_FILE,{});state=clean_state(load_json(STATE_FILE,{}));store_obj=load_json(STORES_FILE,{"stores":[]});stores=store_obj.get("stores",[]);alerts=load_json(ALERTS_FILE,[]);home_obj=store_obj.get("home",{});home=(home_obj.get("lat"),home_obj.get("lng")) if home_obj.get("lat") is not None else None
    keywords=config.get("keywords",[]);retailers=[r for r in config.get("retailers",[]) if r in SEARCH_URLS];radius=float(config.get("nearby_radius_miles",12));cooldown=float(config.get("alert_cooldown_hours",1));timeout=int(config.get("search_timeout_seconds",8));ping=os.environ.get("DISCORD_PING","").lower() in ("1","true","yes");map_url=config.get("map_url","");http=requests.Session();http.headers.update(HEADERS);now=datetime.now(timezone.utc);sent=0
    print(f"Retailers this run: {retailers}");print(f"Nearby radius: {radius:.1f} miles | online alerts ignore radius | cooldown: {cooldown:g}h");print("Price thresholds: DISABLED — availability/restock and qualifying Pokémon feeds only");print("Failure policy: FAIL OPEN — direct matching URLs can alert when stock verification is blocked")
    for retailer in retailers:
        urls=list(dict.fromkeys(config.get("seed_urls",{}).get(retailer,[])));seen=set(urls)
        for keyword in keywords:
            print(f"Checking {retailer} / {keyword}")
            for url in discover_products(http,retailer,keyword,timeout):
                if url not in seen:urls.append(url);seen.add(url)
        for url in urls:
            key=f"{retailer}::{url}";previous=state.get(key,{});in_stock=check_product_page(http,url,timeout);title=next((k for k in keywords if any(part in url.lower() for part in k.lower().split() if len(part)>4)),f"{retailer.title()} Pokémon product")
            should_alert=(in_stock is True and previous.get("in_stock") is not True) or (in_stock is None and not recently_alerted(previous,now,max(cooldown,6)));last_alert=previous.get("last_alert")
            if should_alert:
                pins=nearby_stores(stores,retailer,home,radius);record_alert(alerts,"stock" if in_stock is True else "candidate",retailer,title,url,pins,in_stock);send_stock_alert(retailer,title,url,pins,map_url,ping,in_stock);sent+=1;last_alert=now.isoformat()
            state[key]={"pokemon":True,"title":title,"in_stock":in_stock,"last_seen":now.isoformat(),"last_alert":last_alert}
    for source in config.get("niche_sources",[]):
        source_id=source.get("id","niche");label=source.get("name",source_id)
        for url in source.get("seed_urls",[]):
            key=f"niche:{source_id}::{url}";previous=state.get(key,{})
            if not is_pokemon(url+" "+label+" "+source.get("notes","")):continue
            in_stock=check_product_page(http,url,timeout);should_alert=(in_stock is True and previous.get("in_stock") is not True) or (in_stock is None and not recently_alerted(previous,now,max(cooldown,6)));last_alert=previous.get("last_alert")
            if should_alert:record_alert(alerts,"stock" if in_stock is True else "candidate",label,label,url,[],in_stock);send_stock_alert(label,label,url,[],map_url,ping,in_stock);sent+=1;last_alert=now.isoformat()
            state[key]={"pokemon":True,"title":label,"in_stock":in_stock,"last_seen":now.isoformat(),"last_alert":last_alert}
    print("Checking Slickdeals frontpage RSS...")
    for item in feed_items(http,SLICKDEALS_RSS):
        if not is_pokemon(item["title"]+" "+item["body"]):continue
        key=f"slickdeals::{item['url']}"
        if key in state:continue
        state[key]={"seen":True,"pokemon":True,"title":item["title"]};alert("POKÉMON DEAL FEED",item["title"],item["url"],ping=ping);sent+=1
    print("Checking r/PokemonTCGDeals...")
    for item in reddit_items(http):
        if not is_pokemon(item["title"]+" "+item["body"]):continue
        key=f"reddit::{item['url']}"
        if key in state:continue
        state[key]={"seen":True,"pokemon":True,"title":item["title"]};alert("POKÉMON COMMUNITY DEAL",item["title"],item["url"],ping=ping);sent+=1
    kept=[];cutoff=now-timedelta(days=7)
    for item in alerts:
        try:
            ts=datetime.fromisoformat(item["ts"].replace("Z","+00:00"))
            if item.get("kind")!="test" and ts>=cutoff:kept.append(item)
        except Exception:continue
    save_json(STATE_FILE,state);save_json(ALERTS_FILE,kept[:100]);print(f"Done. Alerts sent this run: {sent}. Tracked Pokémon items: {len(state)}");return 0

if __name__=="__main__":sys.exit(main())
