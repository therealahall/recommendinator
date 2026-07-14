# Goodreads (CSV Export)

Imports books from a Goodreads CSV export.

## Content type
- `book`

## Requirements
- A Goodreads CSV export. Generate one at https://www.goodreads.com/review/import.

## Configuration

```yaml
inputs:
  goodreads_csv:
    plugin: goodreads_csv
    path: "/path/to/goodreads_export.csv"
    content_type: "book"
    enabled: true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the Goodreads CSV export file. |

## Notes
- No API key or network access required — pure file import.
- Reads `Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`, `Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.
- Status mapping: `read` → completed, `currently-reading` → currently consuming, anything else → unread.

## Development
- Implementation: [`goodreads_csv.py`](goodreads_csv.py)
- Tests: [`test_goodreads_csv.py`](test_goodreads_csv.py)
- Plugin class: `GoodreadsCsvPlugin`
