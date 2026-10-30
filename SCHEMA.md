# Field Desk listing schema

Reverse-read from the live Field Desk bundle (`/assets/routes-Dz-GLhHZ.js`, built-in array `D`, 101 listings, 23 Oct 2026). The feed uses exactly this shape so Field Desk can use a feed listing anywhere it uses a built-in one.

## Feed envelope (`feed/listings.json`)

| key | type | notes |
|---|---|---|
| `version` | int | goes up by 1 each time the listings change |
| `schema_version` | int | `2` = this document (v2 adds `tier`, `storyCategory`, grade `story` and four layers; see "Schema v2" below). v1 clients keep working |
| `generated_at` | ISO 8601 UTC string | when this version was written |
| `count` | int | `listings.length` |
| `source` | string | plain-language provenance note |
| `feed_url` | string | canonical URL of this file |
| `content_hash` | string | sha256 of the listings, used to skip no-op publishes |
| `geo_attribution` | string | OSM/ODbL credit |
| `enums_ext` | object | v2: enum values added since the baseline (`grade`, `layer` with colors, `tier`, `storyCategory`) |
| `listings` | array | listing objects, described below |

## Listing object

Required fields, all present on every built-in listing:

| field | type | values / notes |
|---|---|---|
| `id` | string | stable slug, the merge key. Built-in ids are never changed |
| `name` | string | display name |
| `grade` | enum | `death` · `diseased` · `split` · `sovereign` (labels Death, Diseased, Split, Sovereign) · v2: `story` (Tier 2 only) |
| `layer` | enum | `spectral` · `mortuary` · `vice` · `mayhem` · `blight` · `media` · `record` (label "Historic record") · v2: `oddity` · `folklore` · `attraction` · `heritage` |
| `kind` | enum | `house` · `commercial` · `investment` |
| `address` | string | street address. Field Desk reads the state from `place` |
| `place` | string | `City, County, ST`. Must end in `, XX` (2-letter US state); Field Desk's state filter parses this |
| `price` | string | display text, e.g. `$245,000`, `Minimum bid $8,000,000`, `Ask` |
| `status` | enum | `Active` · `Pending` · `Sold` · `Stale` · `Watch` (Watch shows as "To watch") |
| `url` | string | primary listing link (may be `""`) |
| `hook` | string | one-line angle |
| `story` | string | long text; Field Desk falls back to `hook` when empty |
| `history` | string | property facts and history; empty shows "History not written yet." |
| `citation` | string | sources, as prose |
| `notes` | string | desk notes (Field Desk's own; our sheet's Field Notes are never published) |
| `facts` | string | short ` · `-separated fact line |
| `images` | array | `{id, era: current\|historic, caption, source, page, rights: reference\|permission\|cleared}` |
| `priority` | number | sort order (high first). Field Desk's default for new items is 50 |
| `logged` | `YYYY-MM-DD` | date first logged |
| `lastChecked` | `YYYY-MM-DD` | date last verified |
| `dossier` | bool | true = "The file is written" |
| `lore` | bool | sourced dark history at that exact address |

Optional fields, used by some built-in listings:

| field | type | values |
|---|---|---|
| `significance` | enum | `landmark` National landmark · `register` National Register · `district` Historic district · `local` Local record |
| `lane` | enum | `institution` · `inn` (Inn and theater) · `death` (Ordinary death) · `attraction` (Failed attraction) · `disaster` · `bundle` (Business included) |
| `figure` | enum | `railroad` Underground Railroad · `war` Civil War · `name` Famous name |
| `ground` | enum | `church` · `cemetery` · `both` |
| `community` | bool | intentional community; shown with `grid` |
| `grid` | enum | `on` · `off` |
| `quiet` | bool | the listing does not disclose the history |
| `bargain` | bool | "Too cheap" |
| `soldOn` | `YYYY-MM-DD` | used with `status: Sold` (sold within 366 days) |
| `photo` | string | URL of the hero photo |
| `extra` | object | **feed-only.** Anything Field Desk doesn't know about. Apps must ignore unknown keys |
| `tier` | int | v2, on every listing: `1` Triple-D, `2` Strong Story. Missing = 1 |
| `storyCategory` | enum | v2, on every listing: `triple-d` · `oddity` · `long-vacant` · `folklore` · `attraction` · `heritage` · `research`. Missing = `triple-d` |

Derived in the app, not stored: the category chip (`crime`, `paranormal`, `abandoned`, `vice`, `funeral`) comes from `layer`, the text, `lore` and `bargain` (function `T`).

### `extra` (feed-only)
- Rows we added: `extra.source = "master-sheet"`, plus `priceStatus` (full price/status text), `verification`, `mapCategory` and `sources` (URLs).
- Built-in rows that match a Master Sheet row: `extra.masterSheet` holds our values (`price`, `status`, `lastChecked`, `url`, `applied`). `extra.links` holds extra source URLs. Our price, status and lastChecked replace the built-in values only when our check date is newer (`applied: true`).

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
| hook | Primary Tragedy / Forensic Hook |
| citation | Archival Citation, with MLS numbers removed |
| facts | Beds/Baths/SqFt |
| logged, lastChecked | Date Logged |
| dossier | true only if an Episode / Research Doc Link exists |
| priority | Priority Score (0–100; 50 if blank). Merged rows keep Field Desk's priority unless it is missing. Tier 2: rescaled, see below |
| tier / storyCategory | Story Tier (AM) / Story Category (AN). Blank Story Tier on a US row = Tier 1 |
| extra.architecturalNotes … significanceNote | AO architectural_notes, AP vacancy_years, AQ folklore_type, AR failure_reason, AS ownership_chain_summary, AT significance (Tier 2 rows only) |

**Never published:** Brazil or any non-US row, Field Notes, MLS # (including inside URLs, case-insensitive), story_depth_score (AU), Triple D/Death/Diseased/Stigma scores, Confidence, all Brazil columns, and anything from the internal build file. `scripts/publish_feed.py` fails the publish if any of these show up. Built-in Field Desk text is passed through unchanged by decision; some of it already mentions MLS numbers, which are public in the live app.

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

Tier 2 `extra` keys (public-safe, from the sheet): `architecturalNotes`, `vacancyYears`, `folkloreType`, `failureReason`, `ownershipChainSummary`, `significanceNote`. The `significance` enum is set only when the sheet text names a National Register number (`register`), a contributing historic district (`district`) or a local landmark (`local`). `lane` is `attraction` or `institution` for Failed Attraction / Closed Institution rows.

**Compatibility.** v1 fields and values are unchanged. A client that does not know a `grade`, `layer` or `storyCategory` value must fall back (`record` layer styling, a neutral badge) and must not crash. A missing `tier` means 1.

## MLS scrub (applies to every published listing, from feed v5 / 2026-10-30)
No MLS number is ever published, including in the built-in Field Desk baseline text (the baseline file itself is not edited):
- "MLS 1234567"-style mentions are removed from all text fields. A bare MLS number left in text becomes "that listing".
- Any URL (`url`, `photo`, image `page`/`url`) that contains an MLS number is dropped. Images whose links are all dropped are removed.
- When a listing's `url` is dropped it falls back to a clean image page, or else `""`. Clients must handle an empty `url`.
- `publish_feed.py` fails validation if any "MLS <number>" text or MLS-number URL remains anywhere in the feed.
