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
"""
import argparse, glob, hashlib, json, os, re, subprocess, sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO, "baseline", "fielddesk_listings.json")
FEED = os.path.join(REPO, "feed", "listings.json")
DEFAULT_SHEET_GLOB = "/workspace/twisted-deeds/Twisted_Deeds_Master_Sheet_*.xlsx"
DEFAULT_INTERNAL = "/workspace/twisted-deeds/_build/properties_internal.json"
FEED_URL = "https://grymmdupp.github.io/twisted-deeds-feed/feed/listings.json"

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
LAYER_MAP = {"mortuary/funerary": "mortuary", "paranormal/spectral": "spectral", "prohibition/vice": "vice",
             "murder/mayhem": "mayhem", "blight/abandonment": "blight"}
STATUS_MAP = {"verified active": "Active", "active": "Active", "pending": "Pending", "sold": "Sold",
              "stale": "Stale", "watch": "Watch"}
REQUIRED = {"id": str, "name": str, "grade": str, "layer": str, "kind": str, "address": str, "place": str,
            "price": str, "status": str, "url": str, "hook": str, "story": str, "history": str, "citation": str,
            "notes": str, "facts": str, "images": list, "priority": (int, float), "logged": str,
            "lastChecked": str, "dossier": bool, "lore": bool}
OPTIONAL = {"significance": str, "photo": str, "lane": str, "quiet": bool, "soldOn": str, "bargain": bool,
            "ground": str, "figure": str, "community": bool, "grid": str, "extra": dict}
FORBIDDEN_KEYS = {"fieldnotes", "field_notes", "mls", "mlsnumber", "scores", "score", "acqscore", "confidence",
                  "triplediscore", "tripled", "death", "diseased", "stigma", "prioritysscore", "priorityscore",
                  "ownershipnotes", "pricebrl", "fx", "brazilflag", "sitevisit", "lat", "lon", "country"}
DEFAULT_PRIORITY = 50  # Field Desk's own default for new listings; our Priority Score is withheld


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
            "pid": get("Property ID"),
        })
    return rows, notes_text, mls_tokens


def iso_date(v):
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v or "")[:10]


def map_row(row, mls_tokens):
    parts = [p.strip() for p in row["ccs"].split("/")]
    place = ", ".join(p for p in parts if p)
    ps = row["priceStatus"]
    price = ps.split(" - ")[0].strip() or "Ask"
    status = STATUS_MAP.get(row["verification"].strip().lower(), "Watch")
    sources = [s.strip() for s in str(row["sources"]).split("|") if s.strip().startswith("http")]
    facts = " · ".join(p.strip() for p in str(row["facts"]).split("/") if p.strip())
    logged = iso_date(row["logged"])
    item = {
        "id": row["pid"] or re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")[:40],
        "name": row["name"], "grade": grade_from_rating(row["rating"]),
        "layer": LAYER_MAP.get(row["category"].strip().lower(), "record"),
        "kind": kind_from(row["name"], ps), "address": row["address"], "place": place,
        "price": price, "status": status, "url": row["url"], "hook": row["hook"], "story": "", "history": "",
        "citation": strip_mls(str(row["citation"]), mls_tokens), "notes": "", "facts": facts, "images": [],
        "priority": DEFAULT_PRIORITY, "logged": logged, "lastChecked": logged,
        "dossier": bool(row["doc"]), "lore": bool(row["hook"]),
        "extra": {"source": "master-sheet", "priceStatus": strip_mls(ps, mls_tokens),
                  "verification": row["verification"], "mapCategory": row["category"],
                  "sources": [s for s in sources if not any(t in s for t in mls_tokens)]},
    }
    return item


def merge_dup(base, ours, mls_tokens=()):
    """Keep Field Desk id/text. Take our price/status/lastChecked only when our check is newer."""
    out = json.loads(json.dumps(base))
    clean = lambda u: u if u and not any(t in u for t in mls_tokens) else ""
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
    stats = {"baseline": len(base["listings"]), "sheet_us_rows": len(rows), "added": len(added),
             "merged": len(merged), "merged_pairs": merged, "added_ids": added}
    return base, listings, stats, notes_text, mls_tokens


def validate(base, listings):
    e = base["enums"]
    enum = {"kind": {x["id"] for x in e["kind"]}, "layer": {x["id"] for x in e["layer"]},
            "grade": {x["id"] for x in e["grade"]}, "status": set(e["status"]),
            "significance": {x["id"] for x in e["significance"]}, "figure": {x["id"] for x in e["figure"]},
            "lane": {x["id"] for x in e["lane"]}, "ground": set(e["ground"]), "grid": set(e["grid"])}
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
        for d in ("logged", "lastChecked", "soldOn"):
            if l.get(d) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", l[d]):
                errs.append(f"{lid}: {d} not YYYY-MM-DD")
        for im in l.get("images", []):
            if im.get("rights") not in set(e["image_rights"]) or im.get("era") not in set(e["image_era"]):
                errs.append(f"{lid}: bad image {im.get('id')}")
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
    for tok in mls_tokens:
        if tok in added_blob:
            errs.append(f"MLS number {tok} found in added data")
    if re.search(r"\bMLS\s*(?:#|No\.?|number)?\s*[A-Z]*\d", added_blob):
        errs.append("'MLS <number>' text found in added data")
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


def listings_hash(listings):
    return hashlib.sha256(json.dumps(listings, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="Master Sheet .xlsx (default: newest match of %s)" % DEFAULT_SHEET_GLOB)
    ap.add_argument("--internal", default=DEFAULT_INTERNAL, help="deny-list file (read only for checks)")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--check", action="store_true", help="build + validate only; write nothing")
    a = ap.parse_args()
    sheet = a.sheet or newest_sheet(DEFAULT_SHEET_GLOB)
    base, listings, stats, notes_text, mls_tokens = build(sheet)
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
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "count": len(listings),
        "source": ("Twisted Deeds listings feed. Field Desk built-in listings (baseline) merged with US rows "
                   "of the Twisted Deeds Master Sheet. Auto-generated by scripts/publish_feed.py; do not edit by hand."),
        "feed_url": FEED_URL,
        "content_hash": h,
        "listings": listings,
    }
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    with open(FEED, "w") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"wrote feed v{feed['version']} ({len(listings)} listings)")
    git = lambda *c: subprocess.run(["git", "-C", REPO, *c], check=True, capture_output=True, text=True).stdout
    git("add", "feed/listings.json")
    if not git("status", "--porcelain", "feed/listings.json").strip():
        print("no git changes")
        return
    git("commit", "-m", f"Feed v{feed['version']}: {len(listings)} listings ({os.path.basename(sheet)})")
    if not a.no_push:
        git("push", "origin", "HEAD:main")
        print("pushed to origin/main")


if __name__ == "__main__":
    main()
