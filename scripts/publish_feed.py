#!/usr/bin/env python3
"""Regenerate feed/listings.json for the Twisted Deeds Field Desk app.

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

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
LAYER_MAP = {"mortuary/funerary": "mortuary", "paranormal/spectral": "spectral", "prohibition/vice": "vice",
             "murder/mayhem": "mayhem", "blight/abandonment": "blight",
             # schema v2 Tier 2 layers
             "architecture/oddity": "oddity", "folklore/legend": "folklore",
             "failed attraction/closed institution": "attraction", "historic/heritage": "heritage"}
SCHEMA_VERSION = 2
# Enum values added in schema v2 (the baseline file is never edited, so they are declared here).
ENUM_EXT = {
    "grade": [{"id": "story", "label": "Strong Story (Tier 2)"}],
    "layer": [{"id": "oddity", "label": "Architecture / Oddity", "color": "#008080"},
              {"id": "folklore", "label": "Folklore / Legend", "color": "#8A9A5B"},
              {"id": "attraction", "label": "Failed Attraction / Closed Institution", "color": "#CC5500"},
              {"id": "heritage", "label": "Historic / Heritage", "color": "#A67C00"}],
    "tier": [{"id": 1, "label": "Tier 1 - Triple-D"}, {"id": 2, "label": "Tier 2 - Strong Story"}],
    "storyCategory": [{"id": "triple-d", "label": "Triple-D"},
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
REQUIRED = {"id": str, "name": str, "grade": str, "layer": str, "kind": str, "address": str, "place": str,
            "price": str, "status": str, "url": str, "hook": str, "story": str, "history": str, "citation": str,
            "notes": str, "facts": str, "images": list, "priority": (int, float), "logged": str,
            "lastChecked": str, "dossier": bool, "lore": bool}
OPTIONAL = {"lat": (int, float), "lon": (int, float), "geo_precision": str, "geo_source": str, "significance": str, "photo": str, "lane": str, "quiet": bool, "soldOn": str, "bargain": bool,
            "ground": str, "figure": str, "community": bool, "grid": str, "extra": dict,
            "tier": int, "storyCategory": str}
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
    item = {
        "id": row["pid"] or re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")[:40],
        "name": row["name"], "grade": "story" if tier == 2 else grade_from_rating(row["rating"]),
        "layer": LAYER_MAP.get(row["category"].strip().lower(), "record"),
        "kind": kind_from(row["name"], ps), "address": row["address"], "place": place,
        "price": price, "status": status, "url": url, "hook": row["hook"], "story": "", "history": "",
        "citation": strip_mls(str(row["citation"]), mls_tokens), "notes": "", "facts": facts, "images": [],
        "priority": to_priority(row.get("priority")), "logged": logged, "lastChecked": logged,
        "dossier": bool(row["doc"]), "lore": bool(row["hook"]),
        "extra": {"source": "master-sheet", "priceStatus": strip_mls(ps, mls_tokens),
                  "verification": row["verification"], "mapCategory": row["category"],
                  "sources": sources},
        "tier": tier,
        "storyCategory": STORY_CAT_MAP.get(str(row.get("storyCategory", "")).strip().lower(),
                                           "triple-d" if tier == 1 else "research"),
    }
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
        sheet["applied"] = True
    else:
        sheet["applied"] = False
    links = [clean(u) for u in [ours["url"]] + ours["extra"]["sources"]]
    links = [u for u in links if u and u != base.get("url")]
    out["extra"] = {**base.get("extra", {}), "masterSheet": sheet, "links": list(dict.fromkeys(links))}
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
            continue
        if ours["id"] in by_id:
            ours["id"] = "td-" + ours["id"]
        by_id[ours["id"]] = len(listings)
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
            if im.get("rights") not in set(e["image_rights"]) or im.get("era") not in set(e["image_era"]):
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
    errs = validate(base, listings) + enforce_exclusions(base, listings, notes_text, mls_tokens, a.internal)
    print(json.dumps({k: v for k, v in stats.items()}, indent=1))
    if errs:
        print("VALIDATION FAILED:\n  " + "\n  ".join(errs))
        sys.exit(1)
    print(f"valid: {len(listings)} listings from {os.path.basename(sheet)}")
    if a.check:
        return
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
        "source": ("Twisted Deeds listings feed. Field Desk built-in listings (baseline) merged with US rows "
                   "of the Twisted Deeds Master Sheet. Auto-generated by scripts/publish_feed.py; do not edit by hand."),
        "feed_url": FEED_URL,
        "geo_attribution": GEO_ATTRIBUTION,
        "content_hash": h,
        "enums_ext": ENUM_EXT,
        "listings": listings,
    }
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    with open(FEED, "w") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"wrote feed v{feed['version']} ({len(listings)} listings)")
    git = lambda *c: subprocess.run(["git", "-C", REPO, *c], check=True, capture_output=True, text=True).stdout
    git("add", "feed/listings.json", "baseline/geocode_cache.json")
    if not git("status", "--porcelain", "feed/listings.json", "baseline/geocode_cache.json").strip():
        print("no git changes")
        return
    git("commit", "-m", f"Feed v{feed['version']}: {len(listings)} listings ({os.path.basename(sheet)})")
    if not a.no_push:
        git("push", "origin", "HEAD:main")
        print("pushed to origin/main")


if __name__ == "__main__":
    main()
