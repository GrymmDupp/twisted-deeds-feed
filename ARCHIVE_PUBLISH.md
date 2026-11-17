# Publishing the Archive shelf (2026-11-16)

The Archive = verified dark-history places that are **not for sale** (haunted-TV leads, sold listings). It is a separate collection,
like Heritage Homes: `feed/archive.json`, advertised by the core feed as `archive: {count, url}`. The app reads it and shows nothing
until it exists (the shelf shows its locked glass case). Zero Archive rows are published today.

## Two ways to publish
**A. Archive-only (use this now): `scripts/publish_archive.py`.** Builds, validates and writes only the Archive rows.
It does not touch the for-sale listings, so the 38 leak-check flags on for-sale rows do not block it.
Its only change to `feed/listings.json` is the 4-line `archive` advert (listings, version and content_hash stay identical).

```
python3 scripts/publish_archive.py --check                       # validate approved staging rows, write nothing
python3 scripts/publish_archive.py --out /tmp/arch               # scratch copy outside the repo
python3 scripts/publish_archive.py                               # write feed/archive.json + advert, commit, push (GitHub Pages deploys the feed)
python3 scripts/publish_archive.py --from sheet                  # rows from the Master Sheet cache (K = Archive) + sidecar; needs a fresh sheet cache
```
Default source = `_data/archive_staging.json`, candidates with `review: "approved"` only (see `_data/archive_staging.README.md`).
After a successful publish the staging file is stamped with `published_on` (private).

**B. Full publish: `scripts/publish_feed.py`.** Also splits Archive rows (collection `archive`) out to `feed/archive.json`, but it validates every
for-sale listing, so it stays blocked while the leak check flags them. It refuses to run if `feed/archive.json` holds rows that are not in the
Master Sheet (so a full publish can never silently drop staging-published places). A **sold listing moving to the Archive** needs this path,
because the row must also leave `listings.json`; `publish_archive.py` refuses an id that is still in `listings.json`.

## What the publisher enforces on Archive rows (publish fails, nothing written)
- 2+ https sources, at least 1 `written`; an episode alone never qualifies; `location_confirmed_by` is required (staging path)
- status `Archive` or `Sold`; price/link may be blank (shown as "Not for sale" / "Sold")
- `media[]` = `{show, network, season, episode, title, year, url}` credit only; https link to where to watch; no listing-portal or MLS URL
- **town-precision / `private_home` rows:** public address = the town only, coordinates rounded to 2 decimals (~1 km), no photos, no listing link,
  no street address anywhere in the public text (regex backstop). A `private_home` row with any precision other than `town` is refused.
  The app draws a dashed town-area circle, never a pin, and also re-applies this rule in the browser.
- the usual MLS, "Triple-D", locked-text and public-key allow-list checks

## Row data (Master Sheet path)
Sheet: K `Archive` (or `Sold`), P and Q blank, Geo Precision `town` for private homes, Full Address = town only.
Sidecar (`_data/sidecar.json`, private) per Site ID: `shelf: "archive"`, `media`, `crew_featured`, `public_road_only`, `private_home`, plus the usual `legacy` block.
`_build/sheet_cache_check.py` allows status `Archive`, blank P/Q for Archive/Sold, and rejects a listing link or a street address on a town-level Archive row.

## Before the first publish
1. Research pause lifted and rows reviewed (`review: "approved"`).
2. `--check`, then `--out /tmp/arch` and look at the JSON.
3. Real publish. The live app shows the shelf on next load (hard refresh or the SW version bump).
