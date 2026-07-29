# Generic CSV Import

Imports content items from a generic CSV file. Each import covers a single content type; recognised columns vary by type. This is a **one-shot file import**, not a source: the file is read once and nothing about it is stored, so import each file whenever you want its contents in your library.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import, set via the `content_type` option)

## Requirements
- A CSV file with at minimum a `title` column for the content type you are importing.
- UTF-8 encoded. A file saved by Excel (UTF-8 with a byte-order mark) works as is.

## Import

Open the **Data** tab, click **Import from file**, pick **CSV Import**, choose the
file, and select a content type. Or from the CLI:

```bash
python3.11 -m src.cli import --source csv_import --file /path/to/library.csv --content-type book
```

Import one file per content type. Web uploads are capped at 50 MB, and the CLI has
no cap.

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `content_type` | str | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## Recognized columns
- Universal: `title`, `status`, `rating`, `date_completed`, `review`, `notes`, `ignored`
- `book`: `author`, `isbn`, `pages`, `year_published`, `genre`
- `movie`: `director`, `year`, `runtime_minutes`, `genre`
- `tv_show`: `creator`, `seasons_watched`, `total_seasons`, `year`, `genre`
- `video_game`: `developer`, `platform`, `genre`, `hours_played`

`status` accepts type-specific aliases (e.g. `read`/`watched`/`played` → completed; `reading`/`watching`/`playing` → currently consuming; `to_read`/`to_watch`/`to_play`/`wishlist`/`unwatched`/`unplayed` → unread). Boolean fields accept `true`/`false`/`1`/`0`/`yes`/`no`. `seasons_watched` accepts a list `[1, 2, 5, 6]` or a count integer.

## Development
- Implementation: [`generic_csv.py`](generic_csv.py)
- Tests: [`test_generic_csv.py`](test_generic_csv.py)
- Plugin class: `CsvImportPlugin`
