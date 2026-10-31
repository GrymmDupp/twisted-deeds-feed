#!/usr/bin/env python3
"""Regenerate feed/listings.json (PUBLIC, preview only) and the PRIVATE full-file export.

Schema v3 (2026-10-30): the public feed carries ONLY the free preview layer of every listing
(id, name, grade, layer, kind, tier, storyCategory, address, place, price, status, url, lat/lon,
logged/lastChecked, priority, summary, facts, loreTeaser, sample images and a few enum flags).
The locked full file (full story/hook, history, timeline, citation + sources, full lore, ownership
chain, the Strong Story long-text fields, every non-sample image, the dossier) is written only to
/workspace/twisted-deeds/_build/listings_full.json, outside this repo and never committed.
check_locked() fails the publish if any locked field or locked text reaches the public feed.

Inputs
  * baseline/fielddesk_listings.json  - Field Desk's built-in listings (never edited here)
  * the newest Twisted_Deeds_Master_Sheet_*.xlsx (US rows only)

Steps: build -> validate schema -> enforce exclusions -> write only if the
listings changed -> git commit + push (unless --no-push).

Exclusions enforced (the script exits non-zero if any is violated):
  * no Brazil / non-US rows (Country column must be USA; place must end in a US state code)
  * no Field Notes column content
  * no MLS numbers (from the MLS # column) or "MLS ####" text in anything we add
  * no internal scores (Triple D, Death/Diseased/Stigma, Confidence, Acquisition, Priority Score)
  * nothing from _build/properties_internal.json (used only as a deny-list, never as a source)
  * no listing URL or source link that contains an MLS number (case-insensitive)

Schema v2 (2026-10-30): every listing carries `tier` (1 = Triple-D, 2 = Strong Story) and
`storyCategory`. Tier 2 rows use grade "story" and the layers oddity/folklore/attraction/heritage
(or blight for long-vacant). Tier 1 always outranks Tier 2: Tier 2 priorities are rescaled into
0..(lowest Tier 1 priority - 1) and validation fails otherwise. Tier 1 priorities are never changed.
"""
import argparse, csv, glob, hashlib, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO, "baseline", "fielddesk_listings.json")
FEED = os.path.join(REPO, "feed", "listings.json")
DEFAULT_SHEET_GLOB = "/workspace/twisted-deeds/Twisted_Deeds_Master_Sheet_*.xlsx"
DEFAULT_INTERNAL = "/workspace/twisted-deeds/_build/properties_internal.json"
DEFAULT_MAP_LAYERS = "/workspace/twisted-deeds/map_layers.csv"
GEOCACHE = os.path.join(REPO, "baseline", "geocode_cache.json")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "twisted-deeds-feed/1.0 (+https://github.com/GrymmDupp/twisted-deeds-feed)"
GEO_ATTRIBUTION = "Geocoding: OpenStreetMap Nominatim, (c) OpenStreetMap contributors, ODbL"
US_BBOX = (17.0, 72.0, -180.0, -60.0)  # lat min/max, lon min/max (US incl. AK/HI/PR)
FEED_URL = "https://grymmdupp.github.io/twisted-deeds-feed/feed/listings.json"
FULL_OUT = "/workspace/twisted-deeds/_build/listings_full.json"   # PRIVATE; never inside the repo, never committed
DOSSIER_DIR = "/workspace/twisted-deeds/dossiers"                 # not exported yet (internal scores inside); see SCHEMA.md

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
LAYER_MAP = {"mortuary/funerary": "mortuary", "paranormal/spectral": "spectral", "prohibition/vice": "vice",
             "murder/mayhem": "mayhem", "blight/abandonment": "blight",
             # schema v2 Tier 2 layers
             "architecture/oddity": "oddity", "folklore/legend": "folklore",
             "failed attraction/closed institution": "attraction", "historic/heritage": "heritage"}
SCHEMA_VERSION = 3
# Enum values added in schema v2 (the baseline file is never edited, so they are declared here).
ENUM_EXT = {
    "grade": [{"id": "story", "label": "Strong Story (Tier 2)"}],
    "layer": [{"id": "oddity", "label": "Architecture / Oddity", "color": "#008080"},
              {"id": "folklore", "label": "Folklore / Legend", "color": "#8A9A5B"},
              {"id": "attraction", "label": "Failed Attraction / Closed Institution", "color": "#CC5500"},
              {"id": "heritage", "label": "Historic / Heritage", "color": "#A67C00"}],
    "tier": [{"id": 1, "label": "Tier 1 - Twisted Deed"}, {"id": 2, "label": "Tier 2 - Strong Story"}],
    "storyCategory": [{"id": "triple-d", "label": "Twisted Deed"},
                      {"id": "oddity", "label": "Architecture / Oddity"},
                      {"id": "long-vacant", "label": "Long-Vacant / Lore"},
                      {"id": "folklore", "label": "Folklore / Legend"},
                      {"id": "attraction", "label": "Failed Attraction / Closed Institution"},
                      {"id": "heritage", "label": "Historic / Heritage"},
                      {"id": "research", "label": "Research Depth"}],
}
STORY_CAT_MAP = {"triple-d": "triple-d", "architecture/oddity": "oddity", "long-vacant/lore": "long-vacant",
                 "folklore/legend": "folklore", "failed attraction/closed institution": "attraction",
                 "historic/heritage": "heritage", "research depth": "research"}
EXTRA_FIELDS = [("architectural_notes", "architecturalNotes"), ("vacancy_years", "vacancyYears"),
                ("folklore_type", "folkloreType"), ("failure_reason", "failureReason"),
                ("ownership_chain_summary", "ownershipChainSummary"), ("significance", "significanceNote")]
# story_depth_score is an internal score and is never read into the feed.
STATUS_MAP = {"verified active": "Active", "active": "Active", "pending": "Pending", "sold": "Sold",
              "stale": "Stale", "watch": "Watch"}


def status_of(v):
    v = (v or "").strip().lower()
    if v in STATUS_MAP:
        return STATUS_MAP[v]
    if v.startswith("verified active"):  # e.g. "Verified Active (2-step) 2026-10-30"
        return "Active"
    return STATUS_MAP.get(v.split(" ")[0], "Watch")
# Schema v3: the PUBLIC listing object. Anything not listed here is never published (allow-list).
REQUIRED = {"id": str, "name": str, "grade": str, "layer": str, "kind": str, "tier": int, "storyCategory": str,
            "address": str, "place": str, "price": str, "status": str, "url": str, "summary": str, "facts": str,
            "loreTeaser": str, "images": list, "priority": (int, float), "logged": str, "lastChecked": str,
            "dossier": bool, "lore": bool}
OPTIONAL = {"lat": (int, float), "lon": (int, float), "geo_precision": str, "geo_source": str, "significance": str,
            "lane": str, "quiet": bool, "soldOn": str, "bargain": bool, "ground": str, "figure": str,
            "community": bool, "grid": str}
PUBLIC_KEYS = set(REQUIRED) | set(OPTIONAL)
PUBLIC_IMAGE_KEYS = {"id", "era", "caption", "source", "page", "rights"}
SAMPLE_RIGHTS = {"permission", "cleared"}          # broker/press "reference" images are never samples
# Listing photos (scripts/listing_photos.py, owner decision 2026-10-30): listing/alt-listing photos are HOTLINKED
# with a credit and a link back to the page they came from (never rehosted); public-domain / CC photos are
# downloaded into photos/ with licence + author. News photos are never used.
PHOTO_IMAGE_KEYS = {"id", "era", "caption", "url", "source", "credit", "image_type", "rights", "author", "license", "license_url"}
PHOTO_RIGHTS = {"hotlink", "public-domain", "cc", "illustration"}
IMAGE_TYPES = {"listing", "alt-listing", "public-domain", "cc", "illustration"}
PHOTO_BASE_URL = "https://grymmdupp.github.io/twisted-deeds-feed/photos/"


def photo_errors(lid, im):
    """Validation for one photo entry in the public feed."""
    errs = []
    if set(im) - PHOTO_IMAGE_KEYS:
        errs.append(f"{lid}: photo {im.get('id')} has non-public keys {sorted(set(im) - PHOTO_IMAGE_KEYS)}")
    t = im.get("image_type")
    if t not in IMAGE_TYPES or im.get("rights") not in PHOTO_RIGHTS:
        errs.append(f"{lid}: photo {im.get('id')} bad image_type/rights {t}/{im.get('rights')}")
    if not str(im.get("url", "")).startswith("https://") and t != "illustration":
        errs.append(f"{lid}: photo {im.get('id')} url must be https")
    if not im.get("credit") or not str(im.get("source", "")).startswith("http"):
        errs.append(f"{lid}: photo {im.get('id')} needs credit + source link")
    if t in ("public-domain", "cc") and (not im.get("license") or not im.get("author") or not im["url"].startswith(PHOTO_BASE_URL)):
        errs.append(f"{lid}: free-licence photo {im.get('id')} needs license, author and a copy in photos/")
    if t in ("listing", "alt-listing") and str(im.get("url", "")).startswith(PHOTO_BASE_URL):
        errs.append(f"{lid}: listing photo {im.get('id')} must be hotlinked, not rehosted")
    return errs
# Locked (full-file) fields. check_locked() fails the publish if any of these keys appears anywhere in the feed.
LOCKED_KEYS = {"hook", "story", "history", "citation", "citations", "notes", "photo", "extra", "sources", "links",
               "timeline", "fullLore", "ownershipChain", "ownershipChainSummary", "ownership_chain",
               "architecturalNotes", "vacancyYears", "folkloreType", "failureReason", "significanceNote",
               "storyFields", "story_fields", "masterSheet", "dossierMd", "dossier_md", "fullFile", "full"}
SUMMARY_MAX, TEASER_MAX = 240, 160
FORBIDDEN_KEYS = {"fieldnotes", "field_notes", "mls", "mlsnumber", "scores", "score", "acqscore", "confidence",
                  "triplediscore", "tripled", "death", "diseased", "stigma", "prioritysscore", "priorityscore",
                  "ownershipnotes", "pricebrl", "fx", "brazilflag", "sitevisit", "lat", "lon", "country",
                  "storydepth", "storydepthscore", "depth", "acquisitionscore"}
DEFAULT_PRIORITY = 50  # Field Desk's own default, used only when our Priority Score is blank


def norm_key(addr):
    a = addr.lower()
    m = re.match(r"\s*(\d+)[-\d]*\s+(?:[nsew]\.?\s+)?([a-z0-9]+)", a)
    z = re.search(r"(\d{5})(?:-\d{4})?\s*$", a)
    return (m.group(1), m.group(2), z.group(1) if z else "") if m else (a, "", "")


def same_place(a, b):
    ka, kb = norm_key(a), norm_key(b)
    if ka[:2] != kb[:2]:
        return False
    return not (ka[2] and kb[2] and ka[2] != kb[2])


def hyperlink_target(v):
    if not v:
        return ""
    m = re.search(r'HYPERLINK\(\s*"([^"]+)"', str(v))
    return m.group(1) if m else (str(v) if str(v).startswith("http") else "")


def grade_from_rating(b):
    b = b or ""
    if "SOVEREIGN" in b.upper():
        return "sovereign"
    counts = {"death": b.count("\U0001F480"), "diseased": b.count("\u2623"), "split": b.count("\U0001F494")}
    best = max(counts.values())
    for g in ("death", "diseased", "split"):
        if counts[g] == best and best > 0:
            return g
    return "death"


def kind_from(name, price):
    t = f"{name} {price}".lower()
    if re.search(r"auction|acre|redevelopment|campus|training center|surplus", t) and "house" not in t:
        return "investment"
    if re.search(r"funeral home|restaurant|\binn\b|courthouse|jail|lodge|commercial|hospital|complex|business", t):
        return "commercial"
    return "house"


def strip_mls(text, mls_tokens):
    text = re.sub(r",?\s*\bMLS\b\s*#?\s*[A-Z]*\d[\w-]*", "", text)
    for tok in mls_tokens:
        text = text.replace(tok, "")
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+,", ",", text)).strip()


def newest_sheet(pattern):
    files = sorted(glob.glob(pattern), key=lambda p: (os.path.basename(p), os.path.getmtime(p)))
    if not files:
        sys.exit(f"No Master Sheet matches {pattern}")
    return files[-1]


def read_sheet(path):
    import openpyxl
    wb_f = openpyxl.load_workbook(path)              # formulas (for HYPERLINK targets)
    wb_v = openpyxl.load_workbook(path, data_only=True)  # values; never saved
    ws_f, ws_v = wb_f["Master Sheet"], wb_v["Master Sheet"]
    hdr = {str(c.value).strip(): c.column for c in ws_v[1] if c.value}
    col = lambda name: hdr.get(name)
    rows, notes_text, mls_tokens = [], [], set()
    for r in range(2, ws_v.max_row + 1):
        get = lambda name: ws_v.cell(r, col(name)).value if col(name) else None
        if not get("Property Name / Identifier"):
            continue
        country = (get("Country") or "USA").strip().upper()
        fn = get("Field Notes")
        if fn:
            notes_text.append(str(fn))
        mls = get("MLS #")
        if mls:
            mls_tokens.update(t for t in re.findall(r"[A-Z]*\d[\w-]{4,}", str(mls)))
        if country not in ("USA", "US", "UNITED STATES"):
            continue  # Brazil and any other non-US rows are internal-only
        rows.append({
            "row": r, "rating": get("Triple D Status / Rating"), "name": get("Property Name / Identifier"),
            "url": hyperlink_target(ws_f.cell(r, col("Listing Link")).value),
            "address": get("Full Address") or "", "ccs": get("City / County / State") or "",
            "priceStatus": get("Asking Price / Status") or "", "hook": get("Primary Tragedy / Forensic Hook") or "",
            "citation": get("Archival Citation") or "", "doc": get("Episode / Research Doc Link"),
            "category": get("Map Category") or "", "verification": get("Verification Status") or "",
            "facts": get("Beds/Baths/SqFt") or "", "logged": get("Date Logged"), "sources": get("Sources") or "",
            "pid": get("Property ID"), "priority": get("Priority Score"),
            "storyTier": get("Story Tier") or "", "storyCategory": get("Story Category") or "",
            "pvSummary": get("Preview Summary") or "", "pvTeaser": get("Lore Teaser") or "",
            "pvSamples": get("Sample Photo Refs") or "",
            **{k: get(k) for k, _ in EXTRA_FIELDS},
        })
    return rows, notes_text, mls_tokens


def iso_date(v):
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v or "")[:10]


def to_priority(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return int(n) if n == int(n) else n


def map_row(row, mls_tokens):
    parts = [p.strip() for p in row["ccs"].split("/")]
    place = ", ".join(p for p in parts if p)
    ps = row["priceStatus"]
    price = ps.split(" - ")[0].strip() or "Ask"
    status = status_of(row["verification"])
    tier = 2 if str(row.get("storyTier", "")).startswith("Tier 2") else 1
    has_mls = lambda u: any(t.lower() in u.lower() for t in mls_tokens)
    sources = [s.strip() for s in str(row["sources"]).split("|") if s.strip().startswith("http")]
    sources = [s for s in sources if not has_mls(s)]
    url = row["url"] if row["url"] and not has_mls(row["url"]) else (sources[0] if sources else "")
    facts = " · ".join(p.strip() for p in str(row["facts"]).split("/") if p.strip())
    logged = iso_date(row["logged"])
    # lastChecked = newest date in the Verification Status (e.g. "Verified Active (rechecked) 2026-10-30"), else Date Logged
    checked = max([logged] + re.findall(r"\d{4}-\d{2}-\d{2}", str(row["verification"] or "")))
    sold = None
    if status == "Sold":
        m = re.search(r"closed ([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})", ps)
        if m:
            try:
                sold = datetime.strptime(m.group(1).replace(".", "")[:3] + " " + m.group(1).split(" ", 1)[1], "%b %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                sold = None
    item = {
        "id": row["pid"] or re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")[:40],
        "name": row["name"], "grade": "story" if tier == 2 else grade_from_rating(row["rating"]),
        "layer": LAYER_MAP.get(row["category"].strip().lower(), "record"),
        "kind": kind_from(row["name"], ps), "address": row["address"], "place": place,
        "price": price, "status": status, "url": url, "hook": row["hook"], "story": "", "history": "",
        "citation": strip_mls(str(row["citation"]), mls_tokens), "notes": "", "facts": facts, "images": [],
        "priority": to_priority(row.get("priority")), "logged": logged, "lastChecked": checked,
        "dossier": bool(row["doc"]), "lore": bool(row["hook"]),
        "extra": {"source": "master-sheet", "priceStatus": strip_mls(ps, mls_tokens),
                  "verification": row["verification"], "mapCategory": row["category"],
                  "sources": sources},
        "tier": tier,
        "storyCategory": STORY_CAT_MAP.get(str(row.get("storyCategory", "")).strip().lower(),
                                           "triple-d" if tier == 1 else "research"),
        # sheet preview columns (AV/AX/AY); used by split_access(), never published under this key
        "_sheet": {"summary": strip_mls(str(row["pvSummary"]), mls_tokens), "teaser": strip_mls(str(row["pvTeaser"]), mls_tokens),
                   "samples": [t.strip() for t in re.split(r"[|;,\n]", str(row["pvSamples"])) if re.fullmatch(r"[\w-]{3,64}", t.strip())]},
    }
    if sold:
        item["soldOn"] = sold
    if tier == 2:
        for k, ek in EXTRA_FIELDS:
            v = row.get(k)
            if v not in (None, ""):
                item["extra"][ek] = strip_mls(str(v), mls_tokens)
        sig = str(row.get("significance") or "")
        if re.search(r"National Register of Historic Places #|is on the National Register|\bNRHP #", sig):
            item["significance"] = "register"
        elif re.search(r"contributing to .*Historic District", sig):
            item["significance"] = "district"
        elif re.search(r"landmark", sig, re.I):
            item["significance"] = "local"
        if item["storyCategory"] == "attraction":
            item["lane"] = "institution" if re.search(r"library|school|hospital|asylum|church", row["name"], re.I) else "attraction"
    return item


def merge_dup(base, ours, mls_tokens=()):
    """Keep Field Desk id/text. Take our price/status/lastChecked only when our check is newer."""
    out = json.loads(json.dumps(base))
    if not isinstance(out.get("priority"), (int, float)):
        out["priority"] = ours["priority"]  # only fill a missing Field Desk priority
    clean = lambda u: u if u and not any(t.lower() in u.lower() for t in mls_tokens) else ""
    sheet = {"name": ours["name"], "price": ours["price"], "status": ours["status"], "url": clean(ours["url"]),
             "lastChecked": ours["lastChecked"], **ours["extra"]}
    if ours["lastChecked"] > base.get("lastChecked", ""):
        out["price"], out["status"], out["lastChecked"] = ours["price"], ours["status"], ours["lastChecked"]
        if ours.get("soldOn"):
            out["soldOn"] = ours["soldOn"]
        sheet["applied"] = True
    else:
        sheet["applied"] = False
    links = [clean(u) for u in [ours["url"]] + ours["extra"]["sources"]]
    links = [u for u in links if u and u != base.get("url")]
    out["extra"] = {**base.get("extra", {}), "masterSheet": sheet, "links": list(dict.fromkeys(links))}
    out["_sheet"] = ours["_sheet"]
    return out


def build(sheet_path):
    base = json.load(open(BASELINE))
    rows, notes_text, mls_tokens = read_sheet(sheet_path)
    listings = [json.loads(json.dumps(l)) for l in base["listings"]]
    by_id = {l["id"]: i for i, l in enumerate(listings)}
    added, merged = [], []
    for row in rows:
        ours = map_row(row, mls_tokens)
        hit = next((i for i, l in enumerate(listings) if same_place(l["address"], ours["address"])), None)
        if hit is not None and hit < len(base["listings"]):
            merged.append((listings[hit]["id"], ours["id"]))
            listings[hit] = merge_dup(listings[hit], ours, mls_tokens)
            listings[hit].setdefault("_sheetRows", []).append(row["row"])
            continue
        if ours["id"] in by_id:
            ours["id"] = "td-" + ours["id"]
        by_id[ours["id"]] = len(listings)
        ours["_sheetRows"] = [row["row"]]
        listings.append(ours)
        added.append(ours["id"])
    for l in listings:  # schema v2: baseline / merged / Tier 1 listings default to Tier 1 Triple-D
        l.setdefault("tier", 1)
        l.setdefault("storyCategory", "triple-d")
    t1 = [l["priority"] for l in listings if l["tier"] == 1]
    ceiling = (min(t1) - 1) if t1 else 99
    for l in listings:
        if l["tier"] == 2:  # sheet priority 0..79 -> 0..ceiling, so Tier 1 always outranks Tier 2
            l["priority"] = round(max(0.0, min(float(l["priority"]), 79.0)) * ceiling / 79.0, 1)
    listings.sort(key=lambda l: l["tier"])  # stable: keeps baseline order inside each tier
    scrubbed, scrub_toks = scrub_mls_published(listings)
    stats_tiers = {"mls_scrubbed_listings": scrubbed, "tier1": len(t1), "tier2": len(listings) - len(t1), "tier2_priority_ceiling": ceiling}
    stats = {**stats_tiers, "baseline": len(base["listings"]), "sheet_us_rows": len(rows), "added": len(added),
             "merged": len(merged), "merged_pairs": merged, "added_ids": added}
    return base, listings, stats, notes_text, mls_tokens


# ---------------------------------------------------------------- MLS scrub (all published listings, baseline included)
MLS_TEXT_RE = re.compile(r"(?:\b(?:OneKey|CCAR|Alaska|under)\s+)?\b[A-Za-z]*MLS[A-Za-z]*(?:\s+Now)?\b\s*(?:#|No\.?|number)?\s*:?\s*([A-Z]{0,6}\d[\w-]{3,})", re.I)
MLS_URL_RES = [re.compile(r"mls[-_/=](?:[a-z]+/)?([A-Za-z]{0,4}\d{5,})", re.I), re.compile(r"/([A-Z]{2}\d{6,})/")]


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def scrub_mls_published(listings):
    """The baseline file is never edited, but nothing with an MLS number is ever PUBLISHED:
    'MLS 123' text is removed from every string, and URLs/images that carry an MLS number are dropped."""
    toks = set()
    for s in _strings(listings):
        toks.update(m.group(1) for m in MLS_TEXT_RE.finditer(s))
        if s.startswith("http"):
            for rx in MLS_URL_RES:
                toks.update(m.group(1) for m in rx.finditer(s))
    toks = {t for t in toks if len(t) >= 5}
    bad_url = lambda u: isinstance(u, str) and u.startswith("http") and (
        any(t.lower() in u.lower() for t in toks) or (re.search(r"mls", u, re.I) and re.search(r"\d{5,}", u)))

    def clean_text(s):
        orig = s
        s = MLS_TEXT_RE.sub("", s)
        for tk in toks:
            s = re.sub(r"\b" + re.escape(tk) + r"\b", "that listing", s)
        if s == orig:
            return s
        s = re.sub(r"\s*/\s*(?=[,.;]|$)", "", s)      # "Tarasa / MLS 1." -> "Tarasa."
        s = re.sub(r"\s+([,.;])", r"\1", s)
        s = re.sub(r",\s*(?=[.;,])", "", s)
        s = re.sub(r"(^|\.\s)\s*[,;]\s*", r"\1", s)
        s = re.sub(r"\(\s*\)", "", s)
        s = re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)
        s = re.sub(r"^\s*[.,;]\s*", "", s)
        s = re.sub(r"\s*·\s*(?=·|$)", "", s).lstrip(" ·")
        return re.sub(r"\s{2,}", " ", s).strip()

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, str):
            return "" if bad_url(o) else (o if o.startswith("http") else clean_text(o))
        return o

    n = 0
    for i, l in enumerate(listings):
        before = json.dumps(l, ensure_ascii=False)
        if isinstance(l.get("images"), list):
            l["images"] = [im for im in l["images"] if not any(bad_url(v) for v in _strings(im))]
        c = clean(l)
        if not c.get("url"):
            alts = [im.get("page") for im in c.get("images", []) if im.get("page")]
            c["url"] = alts[0] if alts else ""
        listings[i] = c
        n += json.dumps(c, ensure_ascii=False) != before
    return n, toks


def validate(base, listings):
    e = base["enums"]
    enum = {"kind": {x["id"] for x in e["kind"]}, "layer": {x["id"] for x in e["layer"]},
            "grade": {x["id"] for x in e["grade"]}, "status": set(e["status"]),
            "significance": {x["id"] for x in e["significance"]}, "figure": {x["id"] for x in e["figure"]},
            "lane": {x["id"] for x in e["lane"]}, "ground": set(e["ground"]), "grid": set(e["grid"])}
    for k in ("grade", "layer"):
        enum[k] |= {x["id"] for x in ENUM_EXT[k]}
    enum["tier"] = {x["id"] for x in ENUM_EXT["tier"]}
    enum["storyCategory"] = {x["id"] for x in ENUM_EXT["storyCategory"]}
    errs, seen = [], set()
    for l in listings:
        lid = l.get("id", "?")
        if lid in seen:
            errs.append(f"duplicate id {lid}")
        seen.add(lid)
        for k, t in REQUIRED.items():
            if not isinstance(l.get(k), t) or (t is str and k in ("id", "name", "address", "price", "status") and not l[k]):
                errs.append(f"{lid}: field {k} missing or wrong type")
        for k, t in OPTIONAL.items():
            if k in l and not isinstance(l[k], t):
                errs.append(f"{lid}: optional {k} wrong type")
        for k, allowed in enum.items():
            if k in l and l[k] not in allowed:
                errs.append(f"{lid}: {k}={l[k]!r} not in {sorted(allowed)}")
        if l.get("tier") == 2 and l.get("grade") != "story":
            errs.append(f"{lid}: Tier 2 listing must use grade 'story'")
        if l.get("tier") == 1 and l.get("grade") == "story":
            errs.append(f"{lid}: grade 'story' is Tier 2 only")
        for d in ("logged", "lastChecked", "soldOn"):
            if l.get(d) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", l[d]):
                errs.append(f"{lid}: {d} not YYYY-MM-DD")
        if "lat" in l or "lon" in l:
            if not all(isinstance(l.get(k), (int, float)) for k in ("lat", "lon")) or not in_us(l["lat"], l["lon"]):
                errs.append(f"{lid}: bad lat/lon")
            if l.get("geo_precision") not in ("address", "listing", "town") or not isinstance(l.get("geo_source"), str):
                errs.append(f"{lid}: bad geo_precision/geo_source")
        for im in l.get("images", []):
            if "image_type" in im:
                errs += photo_errors(lid, im)
                if im.get("era") not in set(e["image_era"]):
                    errs.append(f"{lid}: bad image era {im.get('id')}")
            elif im.get("rights") not in set(e["image_rights"]) or im.get("era") not in set(e["image_era"]):
                errs.append(f"{lid}: bad image {im.get('id')}")
    t1 = [l["priority"] for l in listings if l.get("tier") == 1]
    t2 = [l for l in listings if l.get("tier") == 2]
    if t1 and t2 and max(l["priority"] for l in t2) >= min(t1):
        errs.append("tier rule broken: a Tier 2 priority is >= the lowest Tier 1 priority")
    return errs


def walk_keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_keys(v)


def enforce_exclusions(base, listings, notes_text, mls_tokens, internal_path):
    errs = []
    base_ids = {l["id"] for l in base["listings"]}
    ours = [l for l in listings if l["id"] not in base_ids]
    ours_blob = json.dumps(ours, ensure_ascii=False)
    extras_blob = json.dumps([l.get("extra", {}) for l in listings], ensure_ascii=False)
    added_blob = ours_blob + extras_blob
    for l in listings:
        st = l["place"].rsplit(",", 1)[-1].strip()
        if st not in US_STATES:
            errs.append(f"{l['id']}: place does not end in a US state ({l['place']})")
    if re.search(r"\bBrazil\b|\bBrasil\b|\bBRL\b|R\$\s?\d", added_blob):
        errs.append("Brazil content found in added data")
    for k in walk_keys([l.get("extra", {}) for l in listings]):
        if re.sub(r"[^a-z]", "", k.lower()) in FORBIDDEN_KEYS:
            errs.append(f"forbidden key in extra: {k}")
    low_blob = added_blob.lower()
    for tok in mls_tokens:
        if tok.lower() in low_blob:
            errs.append(f"MLS number {tok} found in added data")
    if re.search(r"\bMLS\s*(?:#|No\.?|number)?\s*[A-Z]*\d", added_blob):
        errs.append("'MLS <number>' text found in added data")
    all_blob = json.dumps(listings, ensure_ascii=False)
    if MLS_TEXT_RE.search(all_blob):
        errs.append("'MLS <number>' text found in published data: " + MLS_TEXT_RE.search(all_blob).group(0))
    for rx in MLS_URL_RES:
        m = rx.search(all_blob)
        if m and len(m.group(1)) >= 5:
            errs.append("MLS number in a published URL: " + m.group(0))
    for note in notes_text:
        for frag in re.split(r"[.;\n]", note):
            frag = frag.strip()
            if len(frag) >= 25 and frag in added_blob:
                errs.append(f"Field Notes text leaked: {frag[:50]}")
    if internal_path and os.path.exists(internal_path):
        props = json.load(open(internal_path)).get("properties", [])
        for p in props:
            for key in ("fieldNotes", "ownershipNotes", "summary", "siteVisit"):
                v = p.get(key)
                if isinstance(v, str) and len(v) >= 25 and v in added_blob:
                    errs.append(f"internal {key} text from {p.get('id')} leaked")
            if str(p.get("country", "")).lower().startswith("bra") and p.get("address") and p["address"] in added_blob:
                errs.append(f"Brazil address {p['address']} leaked")
    return errs



# ---------------------------------------------------------------- v3 access split: public preview vs private full file
# Same rules as the community site's js/feed.js (sentence split, 240/160-char cuts, lore keywords).
SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\u201c(])')
LORE_RE = re.compile(r"ghost|haunt|legend|lore|spirit|apparition|said to|reported|rumou?r|curse|myth|tale|claim", re.I)
YEAR_RE = re.compile(r"\b(1[6-9]\d\d|20[0-2]\d)\b")


def sentences(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return SENT_RE.split(t) if t else []


def cut(t, n):
    t = str(t or "").strip()
    if len(t) <= n:
        return t
    return re.sub(r"[,;:\u2013-]+$", "", re.sub(r"\s+\S*$", "", t[:n])) + "\u2026"


def host_label(u):
    h = urllib.parse.urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def split_access(l):
    """-> (public preview listing, private full-file record shaped like the listing_full table)."""
    ex, sh = l.get("extra", {}) or {}, l.get("_sheet", {}) or {}
    hook_s, story_s = sentences(l.get("hook")), sentences(l.get("story"))
    summary = cut(sh.get("summary") or (hook_s[0] if hook_s else "") or (story_s[0] if story_s else ""), SUMMARY_MAX)
    if sh.get("teaser"):
        teaser = cut(sh["teaser"], TEASER_MAX)
    elif ex.get("folkloreType"):
        teaser = "Lore: " + cut(ex["folkloreType"], TEASER_MAX)
    else:
        teaser = cut(next((x for x in story_s if LORE_RE.search(x)), "")
                     or (hook_s[1] if l.get("tier") == 2 and len(hook_s) > 1 else "")
                     or next((x for x in story_s if not summary.startswith(x[:30])), ""), TEASER_MAX)
    images = l.get("images") or []
    sample_ids = set(sh.get("samples") or [])
    samples = [{k: v for k, v in im.items() if k in PUBLIC_IMAGE_KEYS} for im in images
               if im.get("id") in sample_ids and im.get("rights") in SAMPLE_RIGHTS]
    pub = {k: l[k] for k in PUBLIC_KEYS if k in l and k not in ("summary", "loreTeaser", "images")}
    photos = [{k: v for k, v in im.items() if k in PHOTO_IMAGE_KEYS} for im in (l.get("_photos") or [])]
    pub.update(summary=summary, loreTeaser=teaser, images=photos + samples)
    pub = {k: pub[k] for k in sorted(pub, key=lambda k: (list(REQUIRED) + list(OPTIONAL)).index(k))}

    src, seen = [], set()
    for u in list(ex.get("sources") or []) + list(ex.get("links") or []) + ([ex["masterSheet"]["url"]] if ex.get("masterSheet", {}).get("url") else []):
        if isinstance(u, str) and u.startswith("http") and u not in seen and u != l.get("url"):
            seen.add(u); src.append({"url": u, "label": host_label(u)})
    for im in images:
        u = im.get("page") or ""
        if im.get("rights") == "reference" and u.startswith("http") and u not in seen:
            seen.add(u); src.append({"url": u, "label": f"{im.get('source') or host_label(u)}: listing photos (reference only)"})
    lore = [ex["folkloreType"]] if ex.get("folkloreType") else []
    lore += [x for x in hook_s + story_s if LORE_RE.search(x) and x not in lore]
    own = ex.get("ownershipChainSummary") or ""
    tl, seen_s = [], set()
    for x in hook_s + story_s + sentences(l.get("history")) + sentences(own):
        if x in seen_s:
            continue
        seen_s.add(x)
        m = YEAR_RE.search(x)
        if m:
            tl.append({"year": int(m.group(1)), "text": x})
    tl = sorted(tl, key=lambda t: t["year"])[:14]
    full = {
        "listing_id": l["id"],
        "story": "\n\n".join(x for x in (l.get("hook"), l.get("story")) if x),
        "history": l.get("history") or "",
        "timeline": tl,
        "lore": "\n".join(lore),
        "citations": l.get("citation") or "",
        "sources": src,
        "ownership_chain": own,
        "story_fields": {k: v for k, v in {"architectural_notes": ex.get("architecturalNotes"), "vacancy_years": ex.get("vacancyYears"),
                                           "folklore_type": ex.get("folkloreType"), "failure_reason": ex.get("failureReason"),
                                           "significance": ex.get("significanceNote")}.items() if v},
        "dossier_md": "",   # dossiers/*.md still carry internal scores and listing numbers; not exported yet
        # not listing_full columns (the import script ignores them):
        "images": [im for im in images if im not in samples],
        "photo": l.get("photo") or "",
    }
    return pub, full


def check_locked(public, full_by_id, envelope_keys=()):
    """Fail the publish if any locked (full-file) field or locked text appears in the public feed."""
    errs = []
    for k in envelope_keys:
        if k in LOCKED_KEYS:
            errs.append(f"locked key {k!r} in the feed envelope")
    for l in public:
        lid = l.get("id", "?")
        for k in l:
            if k not in PUBLIC_KEYS:
                errs.append(f"{lid}: non-preview key {k!r} in the public feed")
        for k in walk_keys(l):
            if k in LOCKED_KEYS or k.startswith("_"):
                errs.append(f"{lid}: locked key {k!r} in the public feed")
        for im in l.get("images", []):
            if "image_type" in im:
                errs += photo_errors(lid, im)
            elif im.get("rights") not in SAMPLE_RIGHTS or set(im) - PUBLIC_IMAGE_KEYS:
                errs.append(f"{lid}: image {im.get('id')} is not an approved sample")
        if len(l.get("summary", "")) > SUMMARY_MAX + 1 or len(l.get("loreTeaser", "")) > TEASER_MAX + 8:
            errs.append(f"{lid}: summary/loreTeaser longer than the preview limit")
        f = full_by_id.get(lid)
        if not f:
            errs.append(f"{lid}: no private full-file record")
            continue
        # locked text: any full-file sentence (40+ chars) found outside summary/loreTeaser is a leak
        rest = json.dumps({k: v for k, v in l.items() if k not in ("summary", "loreTeaser")}, ensure_ascii=False)
        texts = [f["story"], f["history"], f["lore"], f["citations"], f["ownership_chain"], *map(str, f["story_fields"].values())]
        for t in texts:
            for x in sentences(t):
                if len(x) >= 40 and x in rest:
                    errs.append(f"{lid}: locked text in public feed: {x[:60]}")
        # a photo entry's own url/source (its credit link back) is allowed; any other locked link is a leak
        rest_np = json.dumps({k: (v if k != "images" else [i for i in v if "image_type" not in i])
                              for k, v in l.items() if k not in ("summary", "loreTeaser")}, ensure_ascii=False)
        for x in f["sources"]:
            if x["url"] != l.get("url") and x["url"] in rest_np:
                errs.append(f"{lid}: locked source URL in public feed: {x['url'][:60]}")
        if f.get("photo") and f["photo"] in rest_np:
            errs.append(f"{lid}: locked photo URL in public feed")
    return errs


def check_full_private(full, notes_text, mls_tokens, internal_path):
    """The private file feeds the paid full file, so it gets the same MLS / Brazil / Field Notes rules."""
    errs, blob = [], json.dumps(full, ensure_ascii=False)
    if MLS_TEXT_RE.search(blob):
        errs.append("full file: 'MLS <number>' text: " + MLS_TEXT_RE.search(blob).group(0))
    for tok in mls_tokens:
        if len(tok) >= 5 and tok.lower() in blob.lower():
            errs.append(f"full file: MLS number {tok}")
    if re.search(r"\bBrazil\b|\bBrasil\b|\bBRL\b|R\$\s?\d", blob):
        errs.append("full file: Brazil content")
    for note in notes_text:
        for frag in re.split(r"[.;\n]", note):
            frag = frag.strip()
            if len(frag) >= 25 and frag in blob:
                errs.append(f"full file: Field Notes text leaked: {frag[:50]}")
    if internal_path and os.path.exists(internal_path):
        for p in json.load(open(internal_path)).get("properties", []):
            for key in ("fieldNotes", "ownershipNotes", "siteVisit"):
                v = p.get(key)
                if isinstance(v, str) and len(v) >= 25 and v in blob:
                    errs.append(f"full file: internal {key} text from {p.get('id')}")
    return errs


def write_full(path, full, sheet):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.commonpath([os.path.abspath(path), REPO]) == REPO:
        sys.exit(f"refusing to write the private full file inside the public repo: {path}")
    out = {"PRIVATE": "Locked full-file content. Never publish or commit. Load into Supabase listing_full with site/supabase/import_full_files.py.",
           "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "sheet": os.path.basename(sheet), "count": len(full), "files": full}
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    print(f"wrote private full file ({len(full)} records): {path}")


# ---------------------------------------------------------------- geocoding
def addr_key(a):
    return re.sub(r"\s+", " ", (a or "").strip().lower())


def in_us(lat, lon):
    return US_BBOX[0] <= lat <= US_BBOX[1] and US_BBOX[2] <= lon <= US_BBOX[3]


class Geocoder:
    """Nominatim with a persistent cache (baseline/geocode_cache.json). 1 request/second max."""

    def __init__(self, path=GEOCACHE, offline=False):
        self.path, self.offline, self.last, self.calls, self.failures = path, offline, 0.0, 0, []
        self.cache = json.load(open(path)) if os.path.exists(path) else {}
        self.dirty = False

    def save(self):
        if self.dirty:
            with open(self.path, "w") as f:
                json.dump(dict(sorted(self.cache.items())), f, ensure_ascii=False, indent=1)
                f.write("\n")

    def _query(self, q):
        ck = "q:" + addr_key(q)
        if ck in self.cache:
            return self.cache[ck]
        if self.offline:
            return None
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        url = NOMINATIM + "?" + urllib.parse.urlencode(
            {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us", "addressdetails": 1})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.load(r)
        except Exception as e:  # network trouble: do not cache, try again next publish
            self.last = time.time()
            self.failures.append(f"{q}: {e}")
            return None
        self.last, self.calls = time.time(), self.calls + 1
        hit = None
        if res:
            x = res[0]
            hit = {"lat": round(float(x["lat"]), 7), "lon": round(float(x["lon"]), 7),
                   "house": bool(x.get("address", {}).get("house_number")),
                   "type": f"{x.get('category', x.get('class', ''))}/{x.get('type', '')}",
                   "display": x.get("display_name", "")}
        self.cache[ck] = hit
        self.dirty = True
        return hit

    def locate(self, listing, seeds):
        a = listing.get("address", "")
        if addr_key(a) in seeds:
            return seeds[addr_key(a)]
        if re.match(r"\s*\d", a):
            hit = self._query(a)
            if hit and hit["house"] and in_us(hit["lat"], hit["lon"]):
                return {"lat": hit["lat"], "lon": hit["lon"], "geo_precision": "address",
                        "geo_source": "OpenStreetMap Nominatim (address match)"}
        parts = [p.strip() for p in listing.get("place", "").split(",") if p.strip()]
        for q in ([f"{parts[0]}, {parts[-1]}"] if len(parts) >= 2 else []) + ([listing["place"]] if parts else []):
            hit = self._query(q)
            if hit and in_us(hit["lat"], hit["lon"]):
                return {"lat": hit["lat"], "lon": hit["lon"], "geo_precision": "town",
                        "geo_source": "OpenStreetMap Nominatim (town centre)"}
        return None


def load_seeds(path, us_addresses):
    """Our own geocodes from map_layers.csv, only for US Master Sheet addresses."""
    seeds = {}
    if not path or not os.path.exists(path):
        return seeds
    want = {addr_key(a) for a in us_addresses}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        k = addr_key(r.get("address"))
        if k not in want:
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError):
            continue
        if not in_us(lat, lon):
            continue
        prec = (r.get("geocode_precision") or "").lower()
        p = "listing" if prec.startswith("listing") else "address" if prec.startswith("address") else "town"
        seeds[k] = {"lat": round(lat, 7), "lon": round(lon, 7), "geo_precision": p,
                    "geo_source": f"Twisted Deeds map layer ({r.get('geocode_precision')})"}
    return seeds


def add_coordinates(listings, seeds, geocoder):
    for l in listings:
        for k in ("lat", "lon", "geo_precision", "geo_source"):
            l.pop(k, None)
        g = geocoder.locate(l, seeds)
        if g:
            l.update(g)
    geocoder.save()


SHEET_PHOTO_HEADER = "Listing Photos"
BACKUP_DIR = "/workspace/twisted-deeds/_backups"


def write_sheet_photos(sheet, row_map):
    """Store each US row's photo URLs in the Master Sheet's "Listing Photos" column (openpyxl keeps formatting).
    One line per photo: '<image_type> | <url> | <credit> | <source>'. Backs the sheet up first, only when it changes."""
    import shutil
    from copy import copy
    from openpyxl import load_workbook
    wb = load_workbook(sheet)
    ws = wb["Master Sheet"]
    hdr = {str(c.value).strip(): c.column for c in ws[1] if c.value}
    col = hdr.get(SHEET_PHOTO_HEADER)
    new_col = col is None
    if new_col:
        col = ws.max_column + 1
        src = ws.cell(1, col - 1)
        h = ws.cell(1, col, SHEET_PHOTO_HEADER)
        h.font, h.fill, h.alignment, h.border = copy(src.font), copy(src.fill), copy(src.alignment), copy(src.border)
        ws.column_dimensions[h.column_letter].width = 60
    changed = new_col
    for r, imgs in row_map.items():
        v = "\n".join(f"{i['image_type']} | {i['url']} | {i['credit']} | {i['source']}" for i in imgs) or None
        c = ws.cell(r, col)
        if (c.value or None) != v:
            c.value, changed = v, True
            ref = ws.cell(r, col - 1 if new_col else max(1, col - 1))
            c.font, c.border, c.fill = copy(ref.font), copy(ref.border), copy(ref.fill)
            c.alignment = copy(ref.alignment).__class__(wrap_text=True, vertical="top")
    if not changed:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.splitext(os.path.basename(sheet))[0]
    shutil.copy2(sheet, os.path.join(BACKUP_DIR, f"{base}.{ts}.pre-photos.xlsx"))
    csvp = os.path.splitext(sheet)[0] + ".csv"
    if os.path.exists(csvp):
        shutil.copy2(csvp, os.path.join(BACKUP_DIR, f"{base}.{ts}.pre-photos.csv"))
    wb.save(sheet)
    exporter = "/workspace/twisted-deeds/_build/export_master_csv.py"
    if os.path.exists(exporter):
        subprocess.run([sys.executable, exporter], check=False)
    print(f"wrote {SHEET_PHOTO_HEADER} for {len(row_map)} sheet rows (backup in {BACKUP_DIR})")


def listings_hash(listings):
    return hashlib.sha256(json.dumps(listings, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="Master Sheet .xlsx (default: newest match of %s)" % DEFAULT_SHEET_GLOB)
    ap.add_argument("--internal", default=DEFAULT_INTERNAL, help="deny-list file (read only for checks)")
    ap.add_argument("--map-layers", default=DEFAULT_MAP_LAYERS, help="our geocodes (US rows only are used)")
    ap.add_argument("--offline-geo", action="store_true", help="use the geocode cache only")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--check", action="store_true", help="build + validate only; write nothing")
    ap.add_argument("--full-out", default=FULL_OUT, help="PRIVATE full-file export (outside the repo)")
    ap.add_argument("--full-only", action="store_true", help="write only the private full file; no feed, no git")
    ap.add_argument("--offline-photos", action="store_true", help="use the photo cache only (no page fetches)")
    ap.add_argument("--refresh-photos", action="store_true", help="re-pull photos for every listing")
    ap.add_argument("--no-sheet-photos", action="store_true", help="do not write the Listing Photos column")
    a = ap.parse_args()
    sheet = a.sheet or newest_sheet(DEFAULT_SHEET_GLOB)
    base, listings, stats, notes_text, mls_tokens = build(sheet)
    us_addrs = [l["address"] for l in listings if l.get("extra", {}).get("source") == "master-sheet"]
    us_addrs += [l["extra"]["masterSheet"].get("address", "") for l in listings if "masterSheet" in l.get("extra", {})]
    seeds = load_seeds(a.map_layers, us_addrs + [l["address"] for l in listings])
    geo = Geocoder(offline=a.offline_geo)
    add_coordinates(listings, seeds, geo)
    from collections import Counter
    stats["geo"] = dict(Counter(l.get("geo_precision", "none") for l in listings))
    stats["geo_new_requests"] = geo.calls
    if geo.failures:
        stats["geo_failures"] = geo.failures
    from listing_photos import add_photos
    stats["photos"] = add_photos(listings, mls_tokens, offline=a.offline_photos, force=a.refresh_photos)
    def _mls_free(im):
        blob = " ".join(str(v) for v in im.values())
        return not any(rx.search(blob) for rx in MLS_URL_RES) and not any(len(t) >= 5 and t.lower() in blob.lower() for t in mls_tokens)
    for l in listings:  # photos whose URL or page link carries an MLS number are dropped (same rule as listing URLs)
        l["_photos"] = [im for im in l.get("_photos", []) if _mls_free(im)]
    stats["photos"]["with_photos_after_mls_scrub"] = sum(1 for l in listings if l["_photos"])
    sheet_photo_map = {r: l.get("_photos") or [] for l in listings for r in l.get("_sheetRows", [])}
    pairs = [split_access(l) for l in listings]
    listings, full = [p for p, _ in pairs], [f for _, f in pairs]
    full_by_id = {f["listing_id"]: f for f in full}
    stats["schema_version"] = SCHEMA_VERSION
    stats["private_full_records"] = len(full)
    errs = (validate(base, listings) + enforce_exclusions(base, listings, notes_text, mls_tokens, a.internal)
            + check_locked(listings, full_by_id) + check_full_private(full, notes_text, mls_tokens, a.internal))
    print(json.dumps({k: v for k, v in stats.items()}, indent=1))
    if errs:
        print("VALIDATION FAILED:\n  " + "\n  ".join(errs))
        sys.exit(1)
    print(f"valid: {len(listings)} preview-only listings from {os.path.basename(sheet)}; locked-field check passed")
    if a.check:
        return
    write_full(a.full_out, full, sheet)
    if a.full_only:
        return
    if not a.no_sheet_photos:
        write_sheet_photos(sheet, sheet_photo_map)
    h = listings_hash(listings)
    old = json.load(open(FEED)) if os.path.exists(FEED) else None
    if old and old.get("content_hash") == h:
        print("feed unchanged; nothing to publish")
        return
    feed = {
        "version": (old.get("version", 0) + 1) if old else 1,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "count": len(listings),
        "source": ("Twisted Deeds listings feed, FREE PREVIEW LAYER ONLY (schema v3). Field Desk built-in listings (baseline) "
                   "merged with US rows of the Twisted Deeds Master Sheet. Full files are not in this feed. "
                   "Auto-generated by scripts/publish_feed.py; do not edit by hand."),
        "feed_url": FEED_URL,
        "geo_attribution": GEO_ATTRIBUTION,
        "content_hash": h,
        "enums_ext": ENUM_EXT,
        "listings": listings,
    }
    bad = check_locked(listings, full_by_id, feed.keys())
    if bad:
        sys.exit("VALIDATION FAILED (envelope):\n  " + "\n  ".join(bad))
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    with open(FEED, "w") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"wrote feed v{feed['version']} ({len(listings)} listings)")
    git = lambda *c: subprocess.run(["git", "-C", REPO, *c], check=True, capture_output=True, text=True).stdout
    tracked = ["feed/listings.json", "baseline/geocode_cache.json", "baseline/photo_cache.json", "photos"]
    os.makedirs(os.path.join(REPO, "photos"), exist_ok=True)
    git("add", "-A", *tracked)
    if not git("status", "--porcelain", *tracked).strip():
        print("no git changes")
        return
    git("commit", "-m", f"Feed v{feed['version']}: {len(listings)} listings ({os.path.basename(sheet)})")
    if not a.no_push:
        git("push", "origin", "HEAD:main")
        print("pushed to origin/main")


if __name__ == "__main__":
    main()
