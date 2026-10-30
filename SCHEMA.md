# Twisted Deeds feed schema (v3: free preview layer only)

The listing shape started as Field Desk's built-in listing (reverse-read from the live bundle `/assets/routes-Dz-GLhHZ.js`, array `D`, 101 listings, 23 Oct 2026).

## Schema v3 (2026-10-30): the public feed is preview-only
Every property has a free preview and a locked full file (unlocked per account on the Twisted Deeds community site). From v3 this **public** feed carries **only the preview layer**:
- **Public (this feed):** `id`, `name`, `grade`, `layer`, `kind`, `tier`, `storyCategory`, `address`, `place`, `price`, `status`, `url`, `lat`/`lon`/`geo_precision`/`geo_source`, `logged`, `lastChecked`, `priority`, `summary` (one-sentence preview, max 240 chars), `facts` (basic facts line), `loreTeaser` (max 160 chars), `images` (listing photos and approved sample images, see below), the flags `dossier`/`lore`, and the optional enum flags listed below.
- **Locked, never in this feed:** the full hook/story, `history`, timeline, `citation` and source links, full lore, ownership chain, the Strong Story long-text fields (`architecturalNotes`, `vacancyYears`, `folkloreType`, `failureReason`, `ownershipChainSummary`, `significanceNote`), the hero `photo` and every non-sample image, the dossier, desk `notes`, and the whole `extra` object.
- **Removed keys vs v2:** `hook`, `story`, `history`, `citation`, `notes`, `photo`, `extra`. Broker/press `reference` image pages are not in `images`. **Added:** `summary`, `loreTeaser`.
- The full-file content is written by `scripts/publish_feed.py` to a **private** file outside this repo (`/workspace/twisted-deeds/_build/listings_full.json`, never committed) and loaded into the site's RLS-protected `listing_full` table.
- `publish_feed.py` **fails the publish** if any locked key appears anywhere in the feed, if a listing has a key outside the preview allow-list, if a non-sample image appears, or if any 40+ character sentence of a listing's full file appears outside its `summary`/`loreTeaser`.

**Client rule (Field Desk and any other app): merge field by field.** A feed value replaces a built-in field only when it is present and non-empty. A preview-only feed must never blank built-in full text. For a feed-only listing, show what the feed has (use `summary` where a hook would go).

**History note:** commits before v3 (feed v1-v5) contain full text in `feed/listings.json`, and `baseline/fielddesk_listings.json` is a copy of Field Desk's built-in listings with their full text. History has not been rewritten.

## Feed envelope (`feed/listings.json`)

| key | type | notes |
|---|---|---|
| `version` | int | goes up by 1 each time the listings change |
| `schema_version` | int | `3` = preview-only (this document). v2 added `tier`, `storyCategory`, grade `story` and four layers |
| `generated_at` | ISO 8601 UTC string | when this version was written |
| `count` | int | `listings.length` |
| `source` | string | plain-language provenance note |
| `feed_url` | string | canonical URL of this file |
| `content_hash` | string | sha256 of the listings, used to skip no-op publishes |
| `geo_attribution` | string | OSM/ODbL credit |
| `enums_ext` | object | v2: enum values added since the baseline (`grade`, `layer` with colors, `tier`, `storyCategory`) |
| `listings` | array | listing objects, described below |

## Listing object (v3, public preview)

Required on every listing:

| field | type | values / notes |
|---|---|---|
| `id` | string | stable slug, the merge key. Built-in ids are never changed |
| `name` | string | display name |
| `grade` | enum | `death` · `diseased` · `split` · `sovereign` · `story` (Tier 2 only) |
| `layer` | enum | `spectral` · `mortuary` · `vice` · `mayhem` · `blight` · `media` · `record` (label "Historic record") · `oddity` · `folklore` · `attraction` · `heritage` |
| `kind` | enum | `house` · `commercial` · `investment` |
| `tier` | int | `1` Triple-D, `2` Strong Story |
| `storyCategory` | enum | `triple-d` · `oddity` · `long-vacant` · `folklore` · `attraction` · `heritage` · `research` |
| `address` | string | street address |
| `place` | string | `City, County, ST`. Must end in `, XX` (2-letter US state) |
| `price` | string | display text, e.g. `$245,000`, `Minimum bid $8,000,000`, `Ask` |
| `status` | enum | `Active` · `Pending` · `Sold` · `Stale` · `Watch` (Watch shows as "To watch") |
| `url` | string | primary public listing link (may be `""`) |
| `summary` | string | v3. One-sentence preview (sheet "Preview Summary" if set, else the first sentence of the hook/story), max 240 chars |
| `facts` | string | basic facts, ` · `-separated |
| `loreTeaser` | string | v3. Short lore teaser (sheet "Lore Teaser", else a lore sentence), max 160 chars; may be `""` |
| `images` | array | Photos, cover first; may be `[]`. Photo entries: `{id, url, source, credit, image_type, rights, caption, era, author?, license?, license_url?}`. `image_type`: `listing` \| `alt-listing` (rights `hotlink`: loaded from the source site, never rehosted) \| `public-domain` \| `cc` (copied to `photos/<id>/` here, with `author`, `license`, `license_url`) \| `illustration`. Up to about 8 per listing, scraped by `scripts/listing_photos.py` (og:image, JSON-LD, gallery; logos, headshots, map tiles and pixels skipped; Zillow, Trulia and Realtor.com not scraped). Approved samples keep the older shape `{id, era, caption, source, page, rights: permission\|cleared}`. Never `reference` (broker/press) images |
| `priority` | number | sort order (high first) |
| `logged` | `YYYY-MM-DD` | date first logged |
| `lastChecked` | `YYYY-MM-DD` | date last verified |
| `dossier` | bool | a written file exists (the file itself is locked) |
| `lore` | bool | sourced dark history at that exact address (the lore itself is locked) |

Optional:

| field | type | values |
|---|---|---|
| `lat`, `lon`, `geo_precision`, `geo_source` | see "Coordinates" | |
| `significance` | enum | `landmark` · `register` · `district` · `local` |
| `lane` | enum | `institution` · `inn` · `death` · `attraction` · `disaster` · `bundle` |
| `figure` | enum | `railroad` · `war` · `name` |
| `ground` | enum | `church` · `cemetery` · `both` |
| `community` | bool | intentional community; shown with `grid` |
| `grid` | enum | `on` · `off` |
| `quiet` | bool | the listing does not disclose the history |
| `bargain` | bool | "Too cheap" |
| `soldOn` | `YYYY-MM-DD` | used with `status: Sold` |

No other keys are published. Clients must still ignore unknown keys.

## Private full-file export (not published)
`_build/listings_full.json` = `{PRIVATE, generated_at, sheet, count, files: [...]}`. Each record matches the site's `listing_full` table: `listing_id`, `story` (full hook + story), `history`, `timeline` `[{year, text}]`, `lore` (newline-separated), `citations`, `sources` `[{url, label}]` (source links and broker photo pages), `ownership_chain`, `story_fields` `{architectural_notes, vacancy_years, folklore_type, failure_reason, significance}`, `dossier_md` (empty for now: `dossiers/*.md` still carry internal scores and listing numbers). Two extra keys the import ignores: `images` (non-sample images) and `photo`. Field Desk desk `notes` are not exported. The same MLS / Brazil / Field Notes checks run on this file.

Derived in the app, not stored: the category chip (`crime`, `paranormal`, `abandoned`, `vice`, `funeral`) comes from `layer`, the text, `lore` and `bargain` (function `T`).

### v2 `extra` (removed in v3)
Up to v2 the feed carried an `extra` object (sheet price/status text, verification, map category, source URLs, `masterSheet` merge info and the Tier 2 long-text fields). In v3 none of it is published; the long-text fields and sources moved to the private full file.

## Other Field Desk data, not in this feed
- The persisted store is `localStorage["twisted-deeds-desk-v1"]` = `{listings, money:{episode, pack, agents}}`. Ledger = listings plus money toggles.
- Spot: user-added listings get id `<slug>-<base36 time>`. Rejects get id `reject-<slug>`, with reason ids `lore`, `sale`, `referral`, `kind`.
- Photos are in IndexedDB `twisted-deeds-shots`. Shared-code sync is in `localStorage["twisted-deeds-shared-code"]` plus server functions.
- Brief: a text export, sorted by priority. Phone: install help.

## Mapping from the Master Sheet (US rows only)
| feed | sheet column |
|---|---|
| id | Property ID |
| name | Property Name / Identifier |
| grade | Triple D Status / Rating: `SOVEREIGN` → sovereign, else the most common of 💀 death / ☣️ diseased / 💔 split |
| layer | Map Category: Mortuary/Funerary→mortuary, Paranormal/Spectral→spectral, Prohibition/Vice→vice, Murder/Mayhem→mayhem, Blight/Abandonment→blight; v2: Architecture/Oddity→oddity, Folklore/Legend→folklore, Failed Attraction/Closed Institution→attraction, Historic/Heritage→heritage |
| kind | keyword rule on name and price: auction/acre/campus/redevelopment → investment; funeral home/inn/restaurant/jail/lodge/commercial → commercial; else house |
| address / place | Full Address / City-County-State (`/` → `,`) |
| price / status | Asking Price / Status (text before ` - `) / Verification Status |
| url | Listing Link (HYPERLINK target). If the link contains an MLS number it is dropped and the first clean source URL is used instead |
| summary | Preview Summary (AV), else first sentence of the hook (Primary Tragedy / Forensic Hook) |
| loreTeaser | Lore Teaser (AX), else derived |
| images | Sample Photo Refs (AY): image ids with `permission`/`cleared` rights only |
| (private) citations, sources, story | Archival Citation, Sources, hook — full file only |
| facts | Beds/Baths/SqFt |
| logged, lastChecked | Date Logged |
| dossier | true only if an Episode / Research Doc Link exists |
| priority | Priority Score (0–100; 50 if blank). Merged rows keep Field Desk's priority unless it is missing. Tier 2: rescaled, see below |
| tier / storyCategory | Story Tier (AM) / Story Category (AN). Blank Story Tier on a US row = Tier 1 |
| (private) story_fields, ownership_chain | AO–AT (Tier 2 rows only) — full file only |

**Never published:** Brazil or any non-US row, Field Notes, MLS # (including inside URLs, case-insensitive), story_depth_score (AU), Triple D/Death/Diseased/Stigma scores, Confidence, all Brazil columns, and anything from the internal build file. `scripts/publish_feed.py` fails the publish if any of these show up. From v3 no built-in long text is published at all.

## Coordinates (added in v3)

Every listing may carry:

| field | type | meaning |
|---|---|---|
| `lat`, `lon` | number | WGS84 decimal degrees (US only) |
| `geo_precision` | `address` \| `listing` \| `town` | `address`: the street address was matched to a house number; `listing`: coordinates published on the listing page; `town`: town or ZIP centre only (draw as approximate) |
| `geo_source` | string | where the point came from, e.g. `OpenStreetMap Nominatim`, `OpenStreetMap Nominatim (town centre)`, `Twisted Deeds map layer (...)` |

If a listing has no coordinates, these fields are left out. The feed envelope has `geo_attribution` (OSM/ODbL credit).
Geocoding happens only when the feed is published (Nominatim, 1 request per second, cached in `baseline/geocode_cache.json`). Clients should never geocode.

## Schema v2: Tier 2 "Strong Story" listings (2026-10-30)

Twisted Deeds now also logs buyable properties with a strong non-Triple-D story. They are Tier 2. Triple-D listings (all 101 built-ins and every Tier 1 sheet row) are Tier 1.

**Priority rule: Tier 1 always outranks Tier 2.** Tier 1 priorities are never changed. A Tier 2 sheet priority (0–79) is rescaled to `round(p × (minTier1 − 1) / 79, 1)`, where `minTier1` is the lowest Tier 1 priority in this feed. Every Tier 2 priority is therefore below every Tier 1 priority. The publish fails if that is not true, if a Tier 2 listing has a grade other than `story`, or if a Tier 1 listing uses `story`. Listings are written Tier 1 first, then Tier 2. Clients should sort by `tier` ascending, then `priority` descending.

| v2 value | meaning | map color |
|---|---|---|
| grade `story` | Strong Story (Tier 2). Show a neutral badge, not a Triple-D badge | — |
| layer `oddity` | Architecture / Oddity | `#008080` teal |
| layer `folklore` | Folklore / Legend | `#8A9A5B` moss |
| layer `attraction` | Failed Attraction / Closed Institution | `#CC5500` burnt orange |
| layer `heritage` | Historic / Heritage (also used for storyCategory `research`) | `#A67C00` antique gold |
| layer `blight` | also used for storyCategory `long-vacant` | `#708090` slate |

Tier 2 long-text keys (v2 published these in `extra`; v3 moves them to the private full file): `architecturalNotes`, `vacancyYears`, `folkloreType`, `failureReason`, `ownershipChainSummary`, `significanceNote`. The `significance` enum is set only when the sheet text names a National Register number (`register`), a contributing historic district (`district`) or a local landmark (`local`). `lane` is `attraction` or `institution` for Failed Attraction / Closed Institution rows.

**Compatibility.** v1 fields and values are unchanged. A client that does not know a `grade`, `layer` or `storyCategory` value must fall back (`record` layer styling, a neutral badge) and must not crash. A missing `tier` means 1.

## MLS scrub (applies to every published listing, from feed v5 / 2026-10-30)
No MLS number is ever published, including in the built-in Field Desk baseline text (the baseline file itself is not edited):
- "MLS 1234567"-style mentions are removed from all text fields. A bare MLS number left in text becomes "that listing".
- Any URL (`url`, `photo`, image `page`/`url`) that contains an MLS number is dropped. Images whose links are all dropped are removed.
- When a listing's `url` is dropped it falls back to a clean image page, or else `""`. Clients must handle an empty `url`.
- `publish_feed.py` fails validation if any "MLS <number>" text or MLS-number URL remains anywhere in the feed.
