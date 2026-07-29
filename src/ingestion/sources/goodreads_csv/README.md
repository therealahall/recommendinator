# Goodreads (CSV Export)

Imports books from a Goodreads CSV export. This is a **one-shot file import**, not
a source: there is nothing to create, configure, or re-sync. To pick up new books,
export again and import again. If you want Goodreads to keep itself up to date,
use [goodreads_rss](../goodreads_rss/README.md) instead.

## Content type
- `book`

## Requirements
- A Goodreads CSV export. Generate one at https://www.goodreads.com/review/import.

## Import

Open the **Data** tab, click **Import from file**, pick **Goodreads (CSV Export)**,
and choose your export. Or from the CLI:

```bash
python3.11 -m src.cli import --source goodreads_csv --file /path/to/goodreads_export.csv
```

This plugin takes no import options, since it is always books. Web uploads are
capped at 50 MB, and the CLI has no cap.

## Notes
- No API key or network access required.
- Reads `Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`, `Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.
- Status mapping: `read` → completed, `currently-reading` → currently consuming, anything else → unread.
- An export re-saved in Excel (UTF-8 with a byte-order mark) imports as is.

## Development
- Implementation: [`goodreads_csv.py`](goodreads_csv.py)
- Tests: [`test_goodreads_csv.py`](test_goodreads_csv.py)
- Plugin class: `GoodreadsCsvPlugin`
