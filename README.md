# twisted-deeds-feed

The public, **auto-generated** listings feed for the Twisted Deeds **Field Desk** app (https://twisteddeeds.grok.me).

**Feed URL:** https://grymmdupp.github.io/twisted-deeds-feed/feed/listings.json
Raw mirror: https://raw.githubusercontent.com/GrymmDupp/twisted-deeds-feed/main/feed/listings.json

Do not edit `feed/listings.json` by hand. `scripts/publish_feed.py` regenerates it.

## What's in it
- `baseline/fielddesk_listings.json`: Field Desk's built-in listings and enums, extracted from its bundle. Kept as-is.
- `feed/listings.json`: the baseline merged with the US rows of the Twisted Deeds Master Sheet. Built-in ids and text are kept. Duplicates are merged by address.
- `SCHEMA.md`: the exact listing schema and the sheet-to-feed mapping.

US listings only. Brazil research, field notes, MLS numbers and internal scores are never published, and the script refuses to publish if any appear.

## Publishing
```
python3 scripts/publish_feed.py            # newest Master Sheet -> validate -> commit + push if changed
python3 scripts/publish_feed.py --check    # build + validate only
python3 scripts/publish_feed.py --sheet path/to/sheet.xlsx --no-push
```
Requires `openpyxl`. CDN caches refresh in about 10 minutes (Pages) or 5 minutes (raw).
