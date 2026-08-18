# Goodreads (CSV Export)

Reads the CSV Goodreads emails you from
https://www.goodreads.com/review/import. Books only.

## Columns read

`Title`, `Author`, `My Rating`, `Exclusive Shelf`, `Date Read`, `My Review`,
`Book Id`, `ISBN`, `ISBN13`, `Number of Pages`, `Year Published`, `Publisher`.

- Status: `read` → completed, `currently-reading` → currently consuming,
  anything else → unread.
- `My Rating` of `0` means unrated.
- `Date Read` is `YYYY/MM/DD`; anything else reads as no date.
- `Book Id` becomes the item's external id.

## Skipped lines

Reported with the file line they were on:

| Reason | What it means |
|---|---|
| `no title` | The `Title` cell was empty. |
| `N fields short of the header` | The row has fewer fields than the header. |

## Development

- Implementation: [`goodreads_csv.py`](goodreads_csv.py)
- Tests: [`test_goodreads_csv.py`](test_goodreads_csv.py)
- Importer class: `GoodreadsCsvImporter`, named `goodreads_csv`
