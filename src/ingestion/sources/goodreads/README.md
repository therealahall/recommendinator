# Goodreads

Imports books from a Goodreads CSV export.

## Content type
- `book`

## Requirements
- A Goodreads CSV export. Generate one at https://www.goodreads.com/review/import.

## Importing

This is a one-shot file import, not a syncable `inputs:` source. Upload the CSV
from the web **Data** tab (**Import from file**) or run:

```bash
python3.11 -m src.cli import --source goodreads --file /path/to/goodreads_export.csv
```

Goodreads takes no import options (it is always books).

## Notes
- No API key or network access required — pure file import.
- Reads `Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`, `Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.
- Status mapping: `read` → completed, `currently-reading` → currently consuming, anything else → unread.

## Development
- Implementation: [`goodreads.py`](goodreads.py)
- Tests: [`test_goodreads.py`](test_goodreads.py)
- Plugin class: `GoodreadsPlugin`
