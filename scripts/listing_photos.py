#!/usr/bin/env python3
"""Listing photos for the public feed (schema v3 `images`, photo entries).

Fallback chain per listing (stops at the first step that yields usable photos):
  1. the listing's own source page (`url`)                 -> image_type "listing"   (hotlinked, credited, linked back)
  2. other listing pages for the same property             -> image_type "alt-listing" (hotlinked, credited, linked back)
     (broker/listing-site pages already on file as reference images or sources)
  3. Wikipedia / Wikimedia Commons (free licences only)    -> image_type "public-domain" | "cc"  (downloaded into photos/)
  4. Library of Congress HABS/HAER/HALS (public domain)    -> image_type "public-domain"       (downloaded into photos/)
  5. hand-picked free photos in photos/curated.json (Commons cc/public-domain, downloaded into photos/<id>/)
  6. nothing usable -> the listing's illustration from illustrations/manifest.json (image_type "illustration"),
     re-attached on every run so cache refreshes never wipe it. Illustrations are drawn by hand, never by this script.
Real photos (listing, alt-listing, cc, public-domain) always win over an illustration.

Rules: listing/broker photos are hotlinked only, never rehosted. News/press photos are never used. Logos, agent
headshots, map tiles, icons and tracking pixels are skipped. Sites that block bots or forbid it (Zillow, Trulia,
Realtor.com) are not fetched. Up to MAX_IMAGES per listing. Internal reasons go to _build/photo_notes.json
(outside the repo), never into the feed. Results are cached in baseline/photo_cache.json (URLs only) and re-pulled
when a listing's lastChecked changes, the entry is older than REFRESH_DAYS, or the listing is new.
"""
import concurrent.futures as cf, hashlib, html as htmllib, json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "baseline", "photo_cache.json")
PHOTO_DIR = os.path.join(REPO, "photos")
PHOTO_BASE_URL = "https://grymmdupp.github.io/twisted-deeds-feed/photos/"
NOTES = "/workspace/twisted-deeds/_build/photo_notes.json"   # PRIVATE/internal; never in the feed repo
MAX_IMAGES = 8
REFRESH_DAYS = 7
CACHE_VERSION = 2
CURATED = os.path.join(REPO, "photos", "curated.json")                   # {id: [free-licence photo entries]}
ILLUS_DIR = os.path.join(REPO, "illustrations")
ILLUS_MANIFEST = os.path.join(ILLUS_DIR, "manifest.json")              # {id: illustration entry}
REAL_TYPES = ("listing", "alt-listing", "public-domain", "cc")
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BOT_UA = "twisted-deeds-feed/1.0 (+https://github.com/GrymmDupp/twisted-deeds-feed; photo credits)"
IMAGE_TYPES = ("listing", "alt-listing", "public-domain", "cc", "illustration")
RIGHTS_FOR = {"listing": "hotlink", "alt-listing": "hotlink", "public-domain": "public-domain", "cc": "cc", "illustration": "illustration"}

BLOCKED = {"zillow.com": "Zillow blocks automated access and its terms forbid scraping",
           "trulia.com": "Trulia (Zillow Group) blocks automated access",
           "realtor.com": "Realtor.com blocks automated access and its terms forbid scraping"}
NEWS = {"nytimes.com", "newsday.com", "rgj.com", "lansingstatejournal.com", "daytondailynews.com", "journalgazette.net",
        "hoodline.com", "robbreport.com", "bgindependentmedia.org", "thestate.com", "freep.com", "detroitnews.com",
        "mlive.com", "cnn.com", "nypost.com", "foxnews.com", "usatoday.com", "washingtonpost.com", "apnews.com",
        "patch.com", "sfgate.com", "chicagotribune.com", "latimes.com", "people.com", "dailymail.co.uk",
        "realtor.com/news", "wral.com", "ksl.com", "fox2detroit.com", "clickondetroit.com", "wxyz.com", "yahoo.com",
        "msn.com", "atlasobscura.com", "architecturaldigest.com", "housebeautiful.com", "veranda.com", "thrillist.com",
        "secretsofthecity.com", "mansionglobal.com", "wsj.com", "businessinsider.com", "fortune.com", "time.com"}
SKIP_IMG = re.compile(r"logo|headshot|head[-_]?shot|agent|avatar|profile|broker[-_]?photo|team[-_]|icon|sprite|favicon|badge|"
                      r"placeholder|blank|spacer|pixel|tracking|beacon|1x1|staticmap|maps\.googleapis|tile|mapbox|"
                      r"/map[s]?/|openstreetmap|arcgis|banner|brand|equal[-_]?housing|realtor[-_]?logo|mls[-_]?logo|"
                      r"social|share[-_]|facebook|twitter|instagram|youtube|linkedin|pinterest|qr[-_]?code|signature|"
                      r"default[-_]|no[-_]?image|noimage|coming[-_]?soon|loading|captcha|gravatar", re.I)
IMG_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|#|$)", re.I)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def host(u):
    h = urllib.parse.urlparse(u or "").netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def on(h, domains):
    return next((d for d in domains if "/" not in d and (h == d or h.endswith("." + d))), None)


def credit_of(u, fallback=""):
    h = host(u)
    known = {"coldwellbanker.com": "Coldwell Banker", "coldwellbankerhomes.com": "Coldwell Banker", "redfin.com": "Redfin",
             "residential.com": "Residential.com", "commercialcafe.com": "CommercialCafe", "crexi.com": "Crexi",
             "showcase.com": "Showcase.com", "landsearch.com": "LandSearch", "realmo.com": "Realmo", "land.us": "Land.us",
             "exprealty.com": "eXp Realty", "expcommercial.com": "eXp Commercial", "howardhanna.com": "Howard Hanna",
             "remax-michigan.com": "RE/MAX Michigan", "commercial.century21.com": "Century 21 Commercial",
             "property.jll.com": "JLL", "propertyshark.com": "PropertyShark", "cityfeet.com": "CityFeet",
             "highrises.com": "Highrises.com", "circaoldhouses.com": "Circa Old Houses", "beangroup.com": "Bean Group",
             "mrcooper.xome.com": "Xome", "briggsfreeman.com": "Briggs Freeman Sotheby's", "homefront.com": "HomeFront",
             "treasury.gov": "U.S. Treasury", "survivalrealty.com": "Survival Realty", "myelisting.com": "MyEListing",
             "trophypa.com": "Trophy Properties PA", "hegg.com": "Hegg Companies", "michiganwhitetailproperties.com": "Michigan Whitetail Properties"}
    for d, n in known.items():
        if h == d or h.endswith("." + d):
            return n
    return fallback or h


def fetch(u, ua=BROWSER_UA, limit=3_000_000, timeout=20, accept="text/html,application/xhtml+xml"):
    req = urllib.request.Request(u, headers={"User-Agent": ua, "Accept": accept, "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.headers.get("Content-Type", ""), r.read(limit)


def absol(base, u):
    u = htmllib.unescape((u or "").strip())
    if not u or u.startswith("data:"):
        return ""
    if u.startswith("//"):
        u = "https:" + u
    return urllib.parse.urljoin(base, u)


def _ld_images(o, out):
    if isinstance(o, list):
        for x in o:
            _ld_images(x, out)
    elif isinstance(o, dict):
        for k, v in o.items():
            if k in ("image", "photo", "photos", "primaryImageOfPage", "contentUrl", "thumbnailUrl") or k.lower() == "image":
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, dict):
                    if isinstance(v.get("url") or v.get("contentUrl"), str):
                        out.append(v.get("url") or v.get("contentUrl"))
                    _ld_images(v, out)
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            out.append(x)
                        elif isinstance(x, dict):
                            if isinstance(x.get("url") or x.get("contentUrl"), str):
                                out.append(x.get("url") or x.get("contentUrl"))
                            _ld_images(x, out)
            elif isinstance(v, (list, dict)) and k not in ("logo", "author", "publisher", "brand", "agent", "seller", "provider"):
                _ld_images(v, out)


def extract(page_url, text):
    """Candidate photo URLs in priority order: og:image, JSON-LD image arrays, gallery <img> tags."""
    cands = []
    for m in re.finditer(r"<meta\b[^>]*>", text, re.I):
        tag = m.group(0)
        p = re.search(r"(?:property|name)\s*=\s*[\"']((?:og|twitter):image(?::url|:src)?)[\"']", tag, re.I)
        c = re.search(r"content\s*=\s*[\"']([^\"']+)", tag, re.I)
        if p and c:
            cands.append(("og", c.group(1)))
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", text, re.I | re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        out = []
        _ld_images(data, out)
        cands += [("ld", u) for u in out]
    for m in re.finditer(r"<img\b[^>]*>", text, re.I):
        tag = m.group(0)
        if re.search(r"(alt|class|id)\s*=\s*[\"'][^\"']*(logo|agent|headshot|avatar|icon|map|brand|broker)", tag, re.I):
            continue
        src = None
        for attr in ("data-src", "data-lazy-src", "data-original", "data-full", "data-large", "src"):
            mm = re.search(attr + r"\s*=\s*[\"']([^\"']+)", tag, re.I)
            if mm and not mm.group(1).startswith("data:"):
                src = mm.group(1); break
        if not src:
            ss = re.search(r"srcset\s*=\s*[\"']([^\"']+)", tag, re.I)
            if ss:
                src = ss.group(1).split(",")[-1].strip().split(" ")[0]
        w = re.search(r"\bwidth\s*=\s*[\"']?(\d+)", tag, re.I)
        if src and (not w or int(w.group(1)) >= 300):
            cands.append(("img", src))
    seen, out = set(), []
    for kind, u in cands:
        u = absol(page_url, u)
        if not u.startswith("https://") and not u.startswith("http://"):
            continue
        key = re.sub(r"[?#].*$", "", u).lower()
        if key in seen or SKIP_IMG.search(urllib.parse.urlparse(u).path + "?" + urllib.parse.urlparse(u).query):
            continue
        if kind == "img" and not IMG_EXT.search(u) and not re.search(r"photo|image|img|media|listing|property|cdn", u, re.I):
            continue
        seen.add(key)
        out.append(u)
    return out


def image_ok(u, referer):
    """Real raster photo of a useful size? (rejects pixels, icons and blocked hotlinks)."""
    try:
        req = urllib.request.Request(u, headers={"User-Agent": BROWSER_UA, "Accept": "image/avif,image/webp,image/*",
                                                 "Range": "bytes=0-65535"})
        with urllib.request.urlopen(req, timeout=15) as r:
            ct = r.headers.get("Content-Type", "").lower()
            head = r.read(65536)
            total = r.headers.get("Content-Range", "").split("/")[-1]
    except Exception:
        return False
    if not ct.startswith("image/") or "svg" in ct or "gif" in ct:
        return False
    size = int(total) if total.isdigit() else len(head)
    if size < 12_000:
        return False
    dims = img_dims(head)
    if dims and (dims[0] < 320 or dims[1] < 200 or dims[0] / max(dims[1], 1) > 3.2 or dims[1] / max(dims[0], 1) > 2.2):
        return False
    return True


def img_dims(b):
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            if b[12:16] == b"VP8X":
                return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
            if b[12:16] == b"VP8 ":
                return int.from_bytes(b[26:28], "little") & 0x3FFF, int.from_bytes(b[28:30], "little") & 0x3FFF
        if b[:2] == b"\xff\xd8":
            i = 2
            while i < len(b) - 9:
                if b[i] != 0xFF:
                    i += 1; continue
                m = b[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
                i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    except Exception:
        pass
    return None


def from_page(lid, page, image_type, mls_tokens, note):
    h = host(page)
    if not page.startswith("http"):
        return []
    b = on(h, BLOCKED)
    if b:
        note.append(f"{image_type} {page}: skipped, {BLOCKED[b]}")
        return []
    if on(h, NEWS):
        note.append(f"{image_type} {page}: news/press page, its photos are not used")
        return []
    try:
        final, ct, body = fetch(page)
    except Exception as e:
        note.append(f"{image_type} {page}: fetch failed ({str(e)[:80]})")
        return []
    if "html" not in ct.lower():
        note.append(f"{image_type} {page}: not an HTML page ({ct})")
        return []
    text = body.decode("utf-8", "ignore")
    if re.search(r"captcha|access denied|are you a robot|px-captcha|cf-chl|request blocked", text[:20000], re.I) and len(text) < 60000:
        note.append(f"{image_type} {page}: bot wall / access denied")
        return []
    kind = page_kind(final, text)
    if kind != "listing":
        note.append(f"{image_type} {page}: {kind}, not a property listing page; its photos are not used")
        return []
    cands = [u for u in extract(final, text) if not any(t.lower() in u.lower() for t in mls_tokens if len(t) >= 5)]
    cands = [u for u in cands if not re.search(r"\bMLS\W*\d{5,}", u, re.I)]
    good = []
    for u in cands[:24]:
        if image_ok(u, page):
            good.append(u)
        if len(good) >= MAX_IMAGES:
            break
    if not good:
        note.append(f"{image_type} {page}: page fetched, no usable photos ({len(cands)} candidates rejected)")
    cr = credit_of(page)
    return [{"id": f"{lid}-{'p' if image_type == 'listing' else 'a'}{i + 1}", "url": u, "source": page, "credit": cr,
             "image_type": image_type, "rights": RIGHTS_FOR[image_type],
             "caption": f"Listing photo {i + 1}" + (f" ({cr})" if cr else ""), "era": "current"} for i, u in enumerate(good)]


LISTING_DOMAINS = {"coldwellbanker.com", "coldwellbankerhomes.com", "redfin.com", "residential.com", "commercialcafe.com",
                   "crexi.com", "showcase.com", "landsearch.com", "realmo.com", "land.us", "exprealty.com", "expcommercial.com",
                   "howardhanna.com", "remax-michigan.com", "remax.com", "century21.com", "property.jll.com", "propertyshark.com",
                   "cityfeet.com", "highrises.com", "circaoldhouses.com", "beangroup.com", "xome.com", "briggsfreeman.com",
                   "homefront.com", "survivalrealty.com", "myelisting.com", "trophypa.com", "hegg.com", "loopnet.com",
                   "homes.com", "auction.com", "hubzu.com", "landwatch.com", "landandfarm.com", "landflip.com", "compass.com",
                   "sothebysrealty.com", "kw.com", "kwcommercial.com", "bhhs.com", "movoto.com", "estately.com", "homesnap.com",
                   "michiganwhitetailproperties.com", "homeslandcountrypropertyforsale.com", "uccommercialproperties.com",
                   "commonwealthcommercial.com", "rdeskwebsite.com", "theknightgroupla.com", "jromerogroup.com",
                   "enggarcia.com", "wandamooney.com", "baltimorehousehunters.com", "lempmansion.com", "garyelminoufi.com",
                   "oldhouses.com", "oldhousedreams.com", "unitedcountry.com", "mossyoakproperties.com", "whitetailproperties.com"}
LISTING_SIGNALS = [r"RealEstateListing|SingleFamilyResidence|\"@type\"\s*:\s*\"(House|Residence|Apartment|Place|Product|Offer)\"",
                   r"\b(sq\.?\s?ft|square feet|sqft)\b", r"\b\d+\s*(beds?|bedrooms?)\b", r"\b(baths?|bathrooms?)\b",
                   r"listing (agent|courtesy|provided)|listed by|listing office|brokerage|realtor|broker", r"\bfor sale\b|\bfor lease\b|\basking price\b|\blist price\b",
                   r"\bacres?\b", r"\bschedule a (tour|showing)\b|request (info|a tour)"]


def page_kind(final, text):
    """'listing' for property-listing pages (brokerage, listing site, auction, government sale); otherwise a reason."""
    h = host(final)
    if on(h, NEWS):
        return "news/press page"
    ogt = re.search(r"<meta[^>]+property=[\"']og:type[\"'][^>]+content=[\"']([^\"']+)", text, re.I) or \
        re.search(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:type", text, re.I)
    article = bool(ogt and ogt.group(1).lower() == "article") or bool(re.search(r"\"@type\"\s*:\s*\"(NewsArticle|Article|BlogPosting|ReportageNewsArticle)\"", text))
    if on(h, LISTING_DOMAINS) or h.endswith(".gov") or re.search(r"realty|realtor|properties|homes|estate|auction|brokerage|land", h):
        return "listing"
    if article:
        return "news/editorial article"
    hits = sum(1 for rx in LISTING_SIGNALS if re.search(rx, text[:400000], re.I))
    return "listing" if hits >= 4 else "not a property listing page (editorial, tour or reference site)"


# ---------------------------------------------------------------- free-licence fallbacks
STOP = set("the a an of and at on in house home mansion building estate property historic former old new hall church "
           "former site lot acres acre street st avenue ave road rd former company co inc llc center centre for sale "
           "with former victorian".split())


def name_tokens(name):
    n = re.sub(r"\b1[6-9]\d\d\b|\b20\d\d\b", " ", name or "")
    return [t for t in re.findall(r"[a-z][a-z'\-]{2,}", n.lower()) if t not in STOP]


def api_json(u):
    _, _, b = fetch(u, ua=BOT_UA, accept="application/json")
    return json.loads(b)


FREE_LIC = re.compile(r"^(public domain|pd|cc0|cc[- ]by(-sa)?([- ]\d(\.\d)?)?|cc by(-sa)? \d\.\d|no restrictions|attribution)", re.I)


def commons_info(file_title):
    q = urllib.parse.urlencode({"action": "query", "titles": file_title, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
                                "iiurlwidth": 1600, "format": "json"})
    pages = api_json("https://commons.wikimedia.org/w/api.php?" + q)["query"]["pages"]
    p = next(iter(pages.values()))
    ii = (p.get("imageinfo") or [None])[0]
    if not ii:
        return None
    md = ii.get("extmetadata", {})
    g = lambda k: re.sub(r"<[^>]+>", "", htmllib.unescape(md.get(k, {}).get("value", ""))).strip()
    lic = g("LicenseShortName")
    if not FREE_LIC.search(lic) or g("NonFree").lower() == "true":
        return None
    if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or ii.get("width", 0) < 500:
        return None
    return {"thumb": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl"), "license": lic,
            "license_url": g("LicenseUrl"), "author": g("Artist")[:120] or "Unknown author", "desc": g("ImageDescription")[:300],
            "pd": bool(re.search(r"public domain|^pd|cc0|no restrictions", lic, re.I))}


def download(lid, src_url, idx):
    os.makedirs(os.path.join(PHOTO_DIR, lid), exist_ok=True)
    ext = ".png" if src_url.lower().endswith(".png") else ".jpg"
    fn = f"{idx:02d}-{hashlib.sha1(src_url.encode()).hexdigest()[:10]}{ext}"
    path = os.path.join(PHOTO_DIR, lid, fn)
    if not os.path.exists(path):
        _, ct, b = fetch(src_url, ua=BOT_UA, limit=8_000_000, accept="image/*")
        if not ct.startswith("image/") or len(b) < 10_000:
            raise ValueError("not an image")
        with open(path, "wb") as f:
            f.write(b)
    return PHOTO_BASE_URL + f"{lid}/{fn}"


BUILDING_RX = re.compile(r"\b(built|constructed|erected|listed on the National Register|NRHP|historic district|house|mansion|building|"
                         r"church|library|theatre|theater|hall|farmhouse|lighthouse|hospital|school|architect)\b", re.I)


def from_wikipedia(l, note, title_only=None):
    city = (l.get("place") or "").split(",")[0].strip()
    toks = [t for t in name_tokens(l.get("name")) if t not in set(name_tokens(city)) | {city.lower()}]
    if len(toks) < 1 or not city:
        note.append("wikimedia: listing name too generic to match an article")
        return []
    q = f'{" ".join(toks)} {city}'
    try:
        res = [{"title": title_only}] if title_only else api_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": q, "srlimit": 5, "format": "json"}))["query"]["search"]
    except Exception as e:
        note.append(f"wikimedia: search failed ({e})")
        return []
    for r in res:
        title = r["title"]
        tt = set(name_tokens(title))
        if not title_only and (not toks or len(set(toks) & tt) < max(1, min(2, len(toks)))):
            continue
        try:
            d = api_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "titles": title, "prop": "pageimages|extracts|images", "piprop": "name", "exintro": 1,
                 "explaintext": 1, "imlimit": 30, "format": "json"}))
            p = next(iter(d["query"]["pages"].values()))
        except Exception:
            continue
        ext = p.get("extract") or ""
        if city.lower() not in ext.lower() or not BUILDING_RX.search(ext[:600]):
            continue
        files = ([f"File:{p['pageimage']}"] if p.get("pageimage") else [])
        files += [i["title"] for i in p.get("images", []) if re.search(r"\.(jpe?g|png)$", i["title"], re.I)
                  and set(name_tokens(i["title"])) & set(toks)]
        out, seen_pages = [], set()
        for f in dict.fromkeys(f.replace(" ", "_") for f in files):
            if len(out) >= 4:
                break
            try:
                info = commons_info(f)
            except Exception:
                info = None
            if not info or info["page"] in seen_pages:
                continue
            seen_pages.add(info["page"])
            try:
                local = download(l["id"], info["thumb"], len(out) + 1)
            except Exception:
                continue
            t = "public-domain" if info["pd"] else "cc"
            out.append({"id": f"{l['id']}-w{len(out) + 1}", "url": local, "source": info["page"],
                        "credit": f"{info['author']}, {info['license']}, via Wikimedia Commons", "author": info["author"],
                        "license": info["license"], "license_url": info["license_url"], "image_type": t, "rights": RIGHTS_FOR[t],
                        "caption": re.sub(r"\s+", " ", re.sub(r"\.(jpe?g|png)$", "", f.split(":", 1)[-1], flags=re.I).replace("_", " ")).strip()[:160], "era": "historic" if re.search(r"\b1[89]\d\d\b|HABS", info["desc"] or "") else "current"})
        if out:
            note.append(f"wikimedia: matched article '{title}'")
            return out
    note.append("wikimedia: no matching article with a free photo")
    return []


def from_loc(l, note):
    toks = name_tokens(l.get("name"))
    parts = [p.strip() for p in (l.get("place") or "").split(",") if p.strip()]
    city = parts[0] if parts else ""
    if not toks or not city:
        return []
    q = urllib.parse.urlencode({"q": f'{" ".join(toks)} {city}', "fa": "partof:historic american buildings survey/historic american engineering record/historic american landscapes survey",
                                "fo": "json", "c": 10})
    try:
        res = api_json("https://www.loc.gov/photos/?" + q).get("results", [])
    except Exception as e:
        note.append(f"loc habs: search failed ({str(e)[:60]})")
        return []
    for r in res:
        title = r.get("title", "")
        county = next((p for p in parts if p.lower().endswith(" county")), "")
        st = parts[-1] if len(parts) > 1 else ""
        own = set(toks) - set(name_tokens(city))
        if not (set(name_tokens(title)) & own) or city.lower() not in title.lower() \
                or (county and county.lower() not in title.lower()) or (st and not re.search(r",\s*" + re.escape(st) + r"\b", title)):
            continue
        imgs = [u for u in r.get("image_url", []) if re.search(r"\.jpg", u)]
        if not imgs:
            continue
        src = re.sub(r"#.*$", "", imgs[-1])
        try:
            local = download(l["id"], src if src.startswith("http") else "https:" + src, 1)
        except Exception:
            continue
        note.append(f"loc habs: matched '{title[:80]}'")
        return [{"id": f"{l['id']}-h1", "url": local, "source": r.get("id") or r.get("url"),
                 "credit": "Historic American Buildings Survey, Library of Congress (public domain)", "author": "Historic American Buildings Survey",
                 "license": "Public domain (U.S. government work)", "license_url": "https://www.loc.gov/pictures/collection/hh/rights.html",
                 "image_type": "public-domain", "rights": "public-domain", "caption": title[:160], "era": "historic"}]
    note.append("loc habs: no matching survey record")
    return []


# ---------------------------------------------------------------- driver
def alt_pages(l):
    pages = []
    for im in l.get("images") or []:
        if im.get("page"):
            pages.append(im["page"])
    ex = l.get("extra") or {}
    pages += [u for u in list(ex.get("sources") or []) + list(ex.get("links") or []) if isinstance(u, str)]
    if (ex.get("masterSheet") or {}).get("url"):
        pages.append(ex["masterSheet"]["url"])
    out = []
    for u in pages:
        if u.startswith("http") and u != l.get("url") and u not in out and not on(host(u), NEWS):
            out.append(u)
    return out


def photos_for(l, mls_tokens):
    note = []
    lid = l["id"]
    imgs = from_page(lid, l.get("url") or "", "listing", mls_tokens, note) if l.get("url") else []
    if not l.get("url"):
        note.append("listing: no source URL on file")
    if not imgs:
        for p in alt_pages(l)[:8]:
            if host(p).endswith("wikipedia.org"):
                imgs = from_wikipedia(l, note, title_only=urllib.parse.unquote(p.rsplit("/", 1)[-1]).replace("_", " "))
            else:
                imgs = from_page(lid, p, "alt-listing", mls_tokens, note)
            if imgs:
                break
    if not imgs:
        imgs = from_wikipedia(l, note)
    if not imgs:
        imgs = from_loc(l, note)
    if not imgs:
        note.append("no usable photo found: needs an illustration")
    return imgs[:MAX_IMAGES], note


def load_cache():
    if os.path.exists(CACHE):
        c = json.load(open(CACHE))
        if c.get("version") == CACHE_VERSION:
            return c
    return {"version": CACHE_VERSION, "listings": {}}


def _load_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _local_ok(url, base, root):
    """A rehosted file (photos/ or illustrations/) must exist in the repo; other URLs pass."""
    if not url.startswith(base):
        return True
    rel = urllib.parse.unquote(url[len(base):])
    return ".." not in rel and os.path.isfile(os.path.join(root, rel))


ILLUS_BASE_URL = "https://grymmdupp.github.io/twisted-deeds-feed/illustrations/"


def attach_images(lid, cached, curated, illus):
    """Real photos first (cached scrape results, then curated free photos); the illustration only when none exist."""
    real = [dict(im) for im in cached if im.get("image_type") in REAL_TYPES]
    seen = {im.get("url") for im in real}
    for im in curated.get(lid) or []:
        if im.get("image_type") in ("cc", "public-domain") and im.get("url") not in seen \
                and _local_ok(im.get("url", ""), PHOTO_BASE_URL, PHOTO_DIR):
            real.append(dict(im)); seen.add(im.get("url"))
    if real:
        return real[:MAX_IMAGES]
    e = illus.get(lid)
    if isinstance(e, dict) and e.get("image_type") == "illustration" and e.get("url") \
            and _local_ok(e["url"], ILLUS_BASE_URL, ILLUS_DIR):
        return [dict(e)]
    return []


def add_photos(listings, mls_tokens, offline=False, force=False, workers=8):
    """Attach photo entries to each listing under `_photos` (split_access publishes them in `images`)."""
    cache = load_cache()
    C = cache["listings"]
    stale_before = (now() - timedelta(days=REFRESH_DAYS)).isoformat()
    todo = []
    for l in listings:
        e = C.get(l["id"])
        if force or not e or e.get("url") != l.get("url") or e.get("lastChecked") != l.get("lastChecked") or e.get("checked_at", "") < stale_before:
            todo.append(l)
    notes = json.load(open(NOTES)) if os.path.exists(NOTES) else {}
    if todo and not offline:
        with cf.ThreadPoolExecutor(workers) as ex:
            for l, (imgs, note) in zip(todo, ex.map(lambda x: photos_for(x, mls_tokens), todo)):
                C[l["id"]] = {"url": l.get("url"), "lastChecked": l.get("lastChecked"), "checked_at": now().isoformat(), "images": imgs}
                notes[l["id"]] = {"name": l.get("name"), "checked_at": now().isoformat(), "notes": note}
        with open(CACHE, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True); f.write("\n")
        os.makedirs(os.path.dirname(NOTES), exist_ok=True)
        with open(NOTES, "w") as f:
            json.dump({k: notes[k] for k in sorted(notes)}, f, ensure_ascii=False, indent=1); f.write("\n")
    ids = {l["id"] for l in listings}
    curated, illus = _load_json(CURATED), _load_json(ILLUS_MANIFEST)
    for l in listings:
        l["_photos"] = attach_images(l["id"], C.get(l["id"], {}).get("images") or [], curated, illus)
    return {"photo_refreshed": 0 if offline else len(todo), "with_photos": sum(1 for l in listings if l["_photos"]),
            "by_type": dict(__import__("collections").Counter(im["image_type"] for l in listings for im in l["_photos"][:1])),
            "stale_cache_ids": sorted(set(C) - ids)}
