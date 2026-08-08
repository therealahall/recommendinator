# Goodreads (CSV Export)

Imports books from a Goodreads CSV export.

## Content type
- `book`

## Requirements
- A Goodreads CSV export. Generate one at https://www.goodreads.com/review/import.

## Configuration

```bash
python3.11 -m src.cli source create goodreads_csv goodreads_csv
python3.11 -m src.cli source set goodreads_csv path inputs/goodreads_export.csv
```

Or add it from the **Data** tab with **+ Add source**.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the Goodreads CSV export file, under an allowed source root. |

`path` must resolve under `security.allowed_source_roots` in `config.yaml`,
which defaults to `inputs/`. Keeping the file elsewhere means adding that
directory to the list — see
[SECURITY.md](../../../../docs/SECURITY.md#where-file-imports-may-read).

## Notes
- No API key or network access required — pure file import.
- Reads `Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`, `Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.
- Status mapping: `read` → completed, `currently-reading` → currently consuming, anything else → unread.

## Development
- Implementation: [`goodreads_csv.py`](goodreads_csv.py)
- Tests: [`test_goodreads_csv.py`](test_goodreads_csv.py)
- Plugin class: `GoodreadsCsvPlugin`
