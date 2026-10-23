# Field Desk listing schema

Reverse-read from the live Field Desk bundle (`/assets/routes-Dz-GLhHZ.js`, built-in array `D`, 101 listings, 23 Oct 2026). The feed uses exactly this shape so Field Desk can use a feed listing anywhere it uses a built-in one.

## Feed envelope (`feed/listings.json`)

| key | type | notes |
|---|---|---|
| `version` | int | goes up by 1 each time the listings change |
| `schema_version` | int | `1` = this document |
| `generated_at` | ISO 8601 UTC string | when this version was written |
| `count` | int | `listings.length` |
| `source` | string | plain-language provenance note |
| `feed_url` | string | canonical URL of this file |
| `content_hash` | string | sha256 of the listings, used to skip no-op publishes |
| `listings` | array | listing objects, described below |

## Listing object

Required fields, all present on every built-in listing:

| field | type | values / notes |
|---|---|---|
| `id` | string | stable slug, the merge key. Built-in ids are never changed |
| `name` | string | display name |
| `grade` | enum | `death` · `diseased` · `split` · `sovereign` (labels Death, Diseased, Split, Sovereign) |
| `layer` | enum | `spectral` · `mortuary` · `vice` · `mayhem` · `blight` · `media` · `record` (label "Historic record") |
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
| layer | Map Category: Mortuary/Funerary→mortuary, Paranormal/Spectral→spectral, Prohibition/Vice→vice, Murder/Mayhem→mayhem, Blight/Abandonment→blight |
| kind | keyword rule on name and price: auction/acre/campus/redevelopment → investment; funeral home/inn/restaurant/jail/lodge/commercial → commercial; else house |
| address / place | Full Address / City-County-State (`/` → `,`) |
| price / status | Asking Price / Status (text before ` - `) / Verification Status |
| url | Listing Link (HYPERLINK target) |
| hook | Primary Tragedy / Forensic Hook |
| citation | Archival Citation, with MLS numbers removed |
| facts | Beds/Baths/SqFt |
| logged, lastChecked | Date Logged |
| dossier | true only if an Episode / Research Doc Link exists |
| priority | Priority Score (0–100; 50 if blank). Merged rows keep Field Desk's priority unless it is missing |

**Never published:** Brazil or any non-US row, Field Notes, MLS #, Triple D/Death/Diseased/Stigma scores, Confidence, all Brazil columns, and anything from the internal build file. `scripts/publish_feed.py` fails the publish if any of these show up. Built-in Field Desk text is passed through unchanged by decision; some of it already mentions MLS numbers, which are public in the live app.

## Coordinates (added in v3)

Every listing may carry:

| field | type | meaning |
|---|---|---|
| `lat`, `lon` | number | WGS84 decimal degrees (US only) |
| `geo_precision` | `address` \| `listing` \| `town` | `address`: the street address was matched to a house number; `listing`: coordinates published on the listing page; `town`: town or ZIP centre only (draw as approximate) |
| `geo_source` | string | where the point came from, e.g. `OpenStreetMap Nominatim`, `OpenStreetMap Nominatim (town centre)`, `Twisted Deeds map layer (...)` |

If a listing has no coordinates, these fields are left out. The feed envelope has `geo_attribution` (OSM/ODbL credit).
Geocoding happens only when the feed is published (Nominatim, 1 request per second, cached in `baseline/geocode_cache.json`). Clients should never geocode.
