# Generic CSV Import

Imports content items from a generic CSV file. Each input maps to a single content type; recognised columns vary by type.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import — set via the `content_type` config field)

## Requirements
- A CSV file with at minimum a `title` column for the configured content type.

## Configuration

```yaml
inputs:
  csv_import:
    path: "/path/to/library.csv"
    content_type: "book"   # or movie, tv_show, video_game
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the CSV file. |
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
