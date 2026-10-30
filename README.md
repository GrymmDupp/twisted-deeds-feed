# twisted-deeds-feed

The public, **auto-generated** listings feed for the Twisted Deeds **Field Desk** app (https://twisteddeeds.grok.me).

**Feed URL:** https://grymmdupp.github.io/twisted-deeds-feed/feed/listings.json
Raw mirror: https://raw.githubusercontent.com/GrymmDupp/twisted-deeds-feed/main/feed/listings.json

Do not edit `feed/listings.json` by hand. `scripts/publish_feed.py` regenerates it.

### Schema v3 (2026-10-30): free preview layer only
The feed now carries **only the free preview** of each listing: id, name, grade, layer, kind, tier, storyCategory, address, place, price, status, url, coordinates, dates, priority, a one-sentence `summary`, basic `facts`, a short `loreTeaser` and approved sample images (usually none). The full story, history, timeline, citations and sources, full lore, ownership chain, Strong Story long-text fields, non-sample images and dossiers are **not** in this feed. The script writes them to a private file outside the repo (`_build/listings_full.json`, never committed) for the community site's access-controlled `listing_full` table, and **fails the publish if any locked field appears in the feed**. Clients must merge field by field (a feed value replaces a built-in field only when non-empty). See `SCHEMA.md`.

**History:** commits before v3 (feed v1-v5) still contain full text in `feed/listings.json`, and `baseline/fielddesk_listings.json` holds Field Desk's built-in listings with their full text. History has not been rewritten.

## What's in it
- `baseline/fielddesk_listings.json`: Field Desk's built-in listings and enums, extracted from its bundle. Kept as-is.
- `feed/listings.json`: the preview layer of the baseline merged with the US rows of the Twisted Deeds Master Sheet. Built-in ids are kept. Duplicates are merged by address.
- `SCHEMA.md`: the exact listing schema and the sheet-to-feed mapping.

### Schema v2 (2026-10-30): two tiers
- **Tier 1, Triple-D** (`tier: 1`): death, disease, divorce/stigma, crime and Sovereign. Every built-in listing is Tier 1. This tier is always ranked first.
- **Tier 2, Strong Story** (`tier: 2`, `grade: "story"`): buyable properties with a strong non-Triple-D story (architecture/oddity, folklore, failed attraction or closed institution, heritage, long-vacant). Their priorities are rescaled so they always sit below the lowest Tier 1 priority.
- New layers: `oddity` #008080, `folklore` #8A9A5B, `attraction` #CC5500, `heritage` #A67C00. Long-vacant listings use `blight` #708090. The envelope `enums_ext` lists every added value. Clients must ignore unknown values. See `SCHEMA.md`.

US listings only. Brazil research, field notes, MLS numbers (including inside URLs) and internal scores (including story_depth_score) are never published, and the script refuses to publish if any appear.

## Publishing
```
python3 scripts/publish_feed.py            # newest Master Sheet -> validate -> commit + push if changed
python3 scripts/publish_feed.py --check    # build + validate only (incl. the locked-field check); writes nothing
python3 scripts/publish_feed.py --full-only --offline-geo   # write only the private _build/listings_full.json
python3 scripts/publish_feed.py --sheet path/to/sheet.xlsx --no-push
```
Requires `openpyxl`. CDN caches refresh in about 10 minutes (Pages) or 5 minutes (raw).

### Coordinates
`publish_feed.py` adds `lat`/`lon`/`geo_precision`/`geo_source` to every listing. It uses the internal map-layer seeds where an address matches; otherwise it calls OpenStreetMap Nominatim (1 req/s, with a proper User-Agent). The results are cached in `baseline/geocode_cache.json`, so only new listings trigger requests. Use `--offline-geo` to publish with the cache only. Geocoding data © OpenStreetMap contributors, ODbL.
