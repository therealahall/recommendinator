# Goodreads

Imports books from a Goodreads CSV export.

## Content type
- `book`

## Requirements
- A Goodreads CSV export. Generate one at https://www.goodreads.com/review/import.

## Configuration

```yaml
inputs:
  goodreads:
    path: "/path/to/goodreads_export.csv"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the Goodreads CSV export file. |

## Notes
- No API key or network access required — pure file import.
- Reads `Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`, `Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.
- Status mapping: `read` → completed, `currently-reading` → currently consuming, anything else → unread.

## Development
- Implementation: [`goodreads.py`](goodreads.py)
- Tests: [`test_goodreads.py`](test_goodreads.py)
- Plugin class: `GoodreadsPlugin`
