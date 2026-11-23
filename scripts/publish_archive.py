#!/usr/bin/env python3
"""ARCHIVE-ONLY publish (2026-11-16): ship reviewed Archive rows to feed/archive.json WITHOUT rebuilding, re-validating or
republishing the for-sale listings.

Why it exists: publish_feed.py validates every for-sale listing at once, and its leak check currently flags 38 of them
("internal summary text ... leaked"). This script only builds, validates and writes the Archive rows, so a full publish is not needed
to put the Archive on the live app. It NEVER edits listings (feed/listings.json keeps its listings, version and content_hash;
the only change it makes there is the `archive: {count, url}` advert that tells the app where the shelf lives).

Sources of Archive rows (pick one):
  --from staging   _data/archive_staging.json, candidates with review == "approved" (default)
  --from sheet     Master Sheet cache rows with Status Archive (or sidecar shelf == "archive"), merged with the private sidecar.
                   Needs a fresh, valid sheet cache (same rule as publish_feed.py).

Safety rules (all enforced; any problem exits non-zero and writes nothing):
  * the same schema, MLS, portal-URL, 'Triple-D' and locked-text checks the full publish uses, on the Archive rows only
  * town-precision rows: no street address, coordinates rounded to ~1 km, no photos, no listing URL (publish_feed.archive_privacy / archive_errs)
  * an Archive id that already exists in feed/listings.json is refused (a sold listing moving to the Archive needs the full publish,
    because it must also leave listings.json; fix the 38 leak flags first)
  * nothing is written to the Master Sheet, the sheet outbox, the geocode cache or any listing file

Modes:  --check (build + validate, write nothing) | --out DIR (scratch copy, nothing in the repo) | default = write feed/archive.json,
        add the advert to feed/listings.json, commit and push (add --no-push to commit only).
"""
import argparse, hashlib, json, os, re, subprocess, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_feed as pf
import listing_photos as lp_photos

STAGING = "/workspace/twisted-deeds/_data/archive_staging.json"
STATES = pf.US_STATES


def staged_listing(c):
    """One approved staging candidate -> (public listing dict, private full record). Raises ValueError with a clear message."""
    need = ["site_id", "name", "status", "place", "layer", "story_category", "summary"]
    miss = [k for k in need if not str(c.get(k) or "").strip()]
    if miss:
        raise ValueError(f"{c.get('site_id') or c.get('name') or '?'}: staging candidate is missing {miss}")
    sid, status = c["site_id"], {"archive": "Archive", "sold": "Sold"}.get(str(c["status"]).lower())
    if not status:
        raise ValueError(f"{sid}: status must be 'archive' or 'sold'")
    if str(c.get("shelf", "archive")) != "archive":
        raise ValueError(f"{sid}: shelf must be 'archive'")
    grade = c.get("grade") or "story"
    tier = 2 if grade == "story" else 1
    private = c.get("private_home") is True
    prec = c.get("geo_precision")
    item = {
        "id": sid, "name": c["name"].strip(), "grade": grade, "layer": c["layer"], "kind": c.get("kind") or ("house" if private else "commercial"),
        "tier": tier, "storyCategory": c["story_category"],
        "address": (c.get("address") if prec == "address" and not private and c.get("address") else c["place"]),
        "place": c["place"], "price": "Sold" if status == "Sold" else "Not for sale", "status": status, "url": "",
        "summary": pf.cut(c["summary"], pf.SUMMARY_MAX), "facts": str(c.get("facts") or ""), "loreTeaser": pf.cut(str(c.get("lore_teaser") or ""), pf.TEASER_MAX),
        "images": [], "priority": c.get("priority", 20 if tier == 2 else 60),
        "logged": str(c.get("found") or "")[:10], "lastChecked": str(c.get("found") or "")[:10],
        "dossier": False, "lore": bool(c.get("lore_teaser")),
        "collection": "archive", "shelf": "archive",
    }
    if status == "Sold" and c.get("sold_on"):
        item["soldOn"] = c["sold_on"]
    if c.get("lat") is not None and c.get("lon") is not None:
        item.update(lat=float(c["lat"]), lon=float(c["lon"]), geo_precision=prec or "address", geo_source=str(c.get("geo_source") or "Archive scout (public record)"))
    media, merrs = pf.clean_media(c.get("media"))
    if merrs:
        raise ValueError(f"{sid}: " + "; ".join(merrs))
    if media:
        item["media"] = media
    if c.get("crew_featured") is True:
        item["crew_featured"] = True
    if c.get("public_road_only") is True:
        item["roadOnly"] = True
    if private:
        pf.PRIVATE_HOME_IDS.add(sid)
    srcs = [s for s in c.get("sources") or [] if isinstance(s, dict) and str(s.get("url", "")).startswith("https://")]
    written = [s for s in srcs if s.get("kind") == "written"]
    if len(srcs) < 2 or not written:
        raise ValueError(f"{sid}: needs 2+ https sources, at least 1 written (has {len(srcs)} sources, {len(written)} written)")
    if not str(c.get("location_confirmed_by") or "").strip():
        raise ValueError(f"{sid}: location_confirmed_by is required (a public record or written source, not the show alone)")
    item = {k: item[k] for k in sorted(item, key=lambda k: (list(pf.REQUIRED) + list(pf.OPTIONAL)).index(k))}
    full = {"listing_id": sid, "story": "", "history": "", "timeline": [], "lore": "", "citations": "", "ownership_chain": "", "story_fields": {},
            "sources": [{"url": s["url"], "label": s.get("label") or pf.host_label(s["url"])} for s in srcs], "images": [], "photo": ""}
    return item, full


def from_staging(path):
    d = json.load(open(path))
    rows, fulls, errs = [], {}, []
    for c in d.get("candidates", []):
        if c.get("review") != "approved":
            continue
        try:
            item, full = staged_listing(c)
        except ValueError as e:
            errs.append(str(e)); continue
        rows.append(item); fulls[item["id"]] = full
    return rows, fulls, errs


def from_sheet(sheet, max_age):
    ss = pf._sheet_source()
    errs = ss.scc.validate(sheet, max_age)
    if errs:
        sys.exit("sheet cache invalid:\n  " + "\n  ".join(errs[:30]))
    sites = ss.sites_by_id(path=sheet)
    base, listings, stats, notes_text, mls_tokens = pf.build(sheet)
    rows = [l for l in listings if l.get("collection") == "archive"]
    geo = pf.Geocoder(offline=True)
    pf.sheet_coordinates(rows, sites, geo, save=False)         # sheet pins first; town-only fallback from the cache; never saves
    pairs = [pf.split_access(l) for l in rows]
    rows, fulls = [p for p, _ in pairs], {f["listing_id"]: f for _, f in pairs}
    _, werrs = pf.apply_sheet_wins(rows, sites)
    errs = [e for e in werrs if "sheet row has no listing" not in e]   # every other site is a for-sale row: not our business here
    return rows, fulls, errs, base, notes_text, mls_tokens


def attach_curated_archive_photos(rows):
    """Attach free-licence photos from photos/curated.json. Town-precision and private-home rows stay image-less."""
    curated = lp_photos._load_json(lp_photos.CURATED)
    n = 0
    for r in rows:
        if r.get("geo_precision") == "town" or r["id"] in pf.PRIVATE_HOME_IDS:
            r["images"] = []
            continue
        imgs = lp_photos.attach_images(r["id"], [], curated, {})
        imgs = [im for im in imgs if im.get("image_type") in ("cc", "public-domain") and not lp_photos.image_mls_hits(im)]
        r["images"] = imgs[:3]
        if r["images"]:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", choices=["staging", "sheet"], default="staging")
    ap.add_argument("--staging", default=STAGING)
    ap.add_argument("--sheet-cache", default=pf.DEFAULT_SHEET_CACHE)
    ap.add_argument("--max-cache-age-hours", type=float, default=24.0)
    ap.add_argument("--check", action="store_true", help="build + validate only; write nothing")
    ap.add_argument("--out", help="scratch dir: write archive.json there only (never inside the repo)")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    if a.out and os.path.commonpath([os.path.abspath(a.out), pf.REPO]) == pf.REPO:
        sys.exit("--out must be a scratch directory outside the repo")
    old = json.load(open(pf.FEED))
    base = json.load(open(pf.BASELINE))
    notes_text, mls_tokens = [], set()
    if a.src == "staging":
        rows, fulls, errs = from_staging(a.staging)
        try:
            mls_tokens = pf._sheet_source().mls_tokens()
        except SystemExit:
            pass
    else:
        rows, fulls, errs, base, notes_text, mls_tokens = from_sheet(a.sheet_cache, None if (a.check or a.out) else a.max_cache_age_hours)
    n_photos = attach_curated_archive_photos(rows)
    n_town, priv_errs = pf.archive_privacy(rows)
    existing = {l["id"] for l in old["listings"]}
    errs += priv_errs + pf.ARCHIVE_ERRS
    errs += [f"{r['id']}: already in feed/listings.json. A listing moving to the Archive needs the FULL publish (it must also leave listings.json)" for r in rows if r["id"] in existing]
    errs += pf.validate(base, rows) + pf.enforce_exclusions(base, rows, notes_text, mls_tokens, pf.DEFAULT_INTERNAL) + pf.check_locked(rows, fulls)
    for r in rows:
        if r.get("status") not in ("Archive", "Sold") or r.get("collection") != "archive":
            errs.append(f"{r['id']}: not an Archive row")
        if re.search(r"triple[\s-]?d", json.dumps(r, ensure_ascii=False), re.I):
            errs.append(f"{r['id']}: public text must never say 'Triple-D'")
    print(json.dumps({"source": a.src, "approved_archive_rows": len(rows), "town_precision_sanitised": n_town,
                      "with_photos": sum(1 for r in rows if r.get("images")), "photo_files_attached": n_photos,
                      "with_media": sum(1 for r in rows if r.get("media")), "crew_featured": sum(1 for r in rows if r.get("crew_featured"))}, indent=1))
    if errs:
        print("VALIDATION FAILED (nothing written):\n  " + "\n  ".join(dict.fromkeys(errs)))
        sys.exit(1)
    cur = json.load(open(pf.ARCHIVE_FEED)) if os.path.exists(pf.ARCHIVE_FEED) else None
    h = pf.listings_hash(rows)
    if not rows and not cur:
        print("no reviewed Archive rows: nothing to publish"); return
    if cur and cur.get("content_hash") == h and old.get("archive", {}).get("count") == len(rows):
        print("archive feed unchanged; nothing to publish"); return
    doc = {"version": (cur.get("version", 0) + 1) if cur else 1, "schema_version": pf.SCHEMA_VERSION,
           "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "geo_attribution": pf.GEO_ATTRIBUTION, "enums_ext": pf.ENUM_EXT, "count": len(rows), "collection": "archive",
           "feed_url": pf.ARCHIVE_URL, "core_feed_url": pf.FEED_URL,
           "source": ("Twisted Deeds ARCHIVE shelf, FREE PREVIEW LAYER ONLY. Verified dark-history places that are NOT for sale. "
                      "Town-precision rows carry no street address or exact pin. Published by scripts/publish_archive.py."),
           "content_hash": h, "listings": rows}
    bad = pf.check_locked(rows, fulls, doc.keys())
    if bad:
        sys.exit("VALIDATION FAILED (envelope):\n  " + "\n  ".join(bad))
    if a.check:
        print(f"check OK: would write {len(rows)} Archive rows to feed/archive.json"); return
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        json.dump(doc, open(os.path.join(a.out, "archive.json"), "w"), ensure_ascii=False, indent=1)
        print(f"scratch archive.json written to {a.out}"); return
    os.makedirs(os.path.dirname(pf.ARCHIVE_FEED), exist_ok=True)
    with open(pf.ARCHIVE_FEED, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1); f.write("\n")
    # the ONLY change to feed/listings.json: the advert. Listings, version and content_hash stay identical.
    new = {}
    for k, v in old.items():
        if k == "archive":
            continue
        if k == "listings" and rows:
            new["archive"] = {"count": len(rows), "url": pf.ARCHIVE_URL}
        new[k] = v
    assert new["listings"] == old["listings"] and new["content_hash"] == old["content_hash"] and new["version"] == old["version"]
    if new != old:
        with open(pf.FEED, "w") as f:
            json.dump(new, f, ensure_ascii=False, indent=1); f.write("\n")
    git = lambda *c: subprocess.run(["git", "-C", pf.REPO, *c], check=True, capture_output=True, text=True).stdout
    paths = ["feed/archive.json", "feed/listings.json", "photos", "scripts/publish_archive.py"]
    git("add", "-A", *paths)
    if not git("status", "--porcelain", *paths).strip():
        print("no git changes"); return
    git("commit", "-m", f"Archive shelf v{doc['version']}: {len(rows)} reviewed places, {sum(1 for r in rows if r.get('images'))} with photos (archive-only publish; for-sale listings untouched)")
    if not a.no_push:
        git("push", "origin", "HEAD:main"); print("pushed to origin/main")
    if a.src == "staging":   # private bookkeeping only
        try:
            d = json.load(open(a.staging)); today = datetime.now().strftime("%Y-%m-%d")
            for c in d["candidates"]:
                if c.get("review") == "approved": c["published_on"] = today
            d["updated"] = today; json.dump(d, open(a.staging, "w"), indent=2); os.chmod(a.staging, 0o600)
        except Exception as e:
            print("note: could not stamp staging file:", e)


if __name__ == "__main__":
    main()
