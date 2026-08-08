# Generic CSV Import

Imports content items from a CSV file. One content type per import, with
recognised columns varying by type.

## Requirements

A CSV file with at least a `title` column, and a `content_type` of `book`,
`movie`, `tv_show` or `video_game`.

## Configuration

```bash
python3.11 -m src.cli source create my_csv csv_import
python3.11 -m src.cli source set my_csv path inputs/library.csv
python3.11 -m src.cli source set my_csv content_type book
```

Or use **+ Add source** on the **Data** tab. The id (`my_csv`) is yours to pick,
so you can run several CSV sources over different files.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the CSV file, under an allowed source root. |
| `content_type` | str | yes | One of `book`, `movie`, `tv_show`, `video_game`. |

`path` must resolve under `security.allowed_source_roots` in `config.yaml`,
which defaults to `inputs/`. Keeping the file elsewhere means adding that
directory to the list — see
[SECURITY.md](../../../../docs/SECURITY.md#where-file-imports-may-read).

## Recognized columns

- Universal: `title`, `status`, `rating`, `date_completed`, `review`, `notes`,
  `ignored`
- `book`: `author`, `isbn`, `pages`, `year_published`, `genre`
- `movie`: `director`, `year`, `runtime_minutes`, `genre`
- `tv_show`: `creator`, `seasons_watched`, `total_seasons`, `year`, `genre`
- `video_game`: `developer`, `platform`, `genre`, `hours_played`

`status` takes type-specific aliases: `read`/`watched`/`played` for completed,
`reading`/`watching`/`playing` for currently consuming, and
`to_read`/`to_watch`/`to_play`/`wishlist`/`unwatched`/`unplayed` for unread.
Boolean fields accept `true`/`false`, `1`/`0` and `yes`/`no`. `seasons_watched`
takes a list, `[1, 2, 5, 6]`, or a count integer.

### Which columns win

| Column | Rule |
|---|---|
| `rating`, `review` | Fill-only. Written while the stored value is empty, never overwritten. |
| `status` | Forward-only: unread → consuming → completed. A file can advance a status, never revert a completion. |
| `date_completed` | Replaced only by a later date. |
| `genre` | Additive. An imported genre joins the stored ones. |
| `total_seasons` | Monotonic. It only increases. |
| Everything else | Fill-only, including `seasons_watched` and `notes`. |

One exception to forward-only: raising `total_seasons` above a completed show's
watched-season list sends it back to in-progress. That is a season rule, not a
status one.

Fill-only means editing the value in an export and re-importing does nothing. Use
the edit modal or `library edit --seasons-watched` to change a season checklist.
`notes` is the one that surprises people: a stored note always wins, and no
surface in the app edits one, so the first import of a note is the last word.

`hours_played` is stored and exported as your own playtime. It is not a game
length, so it does not affect recommendations. The
[length scorer](../../../../docs/SCORING.md#content-length-preferences) reads
RAWG's average playtime instead.

### `ignored`

Leave the column out, or a cell blank, and the import says nothing about the
flag, so an item you ignored in the app stays ignored. Write a real value and it
wins in either direction, `false` un-ignoring. The blank case matters because a
CSV header applies to every row: without it, a file carrying the column would
clear the flag on every row it left empty.

**That protects a CSV you maintain by hand. It protects nothing about this app's
own exports**, which write a real `true` or `false` on every row. Re-importing
one replaces your whole ignore list with its state at export time, clearing
anything ignored since. That is the bulk un-ignore path, and it makes an export a
snapshot rather than a patch. Never leave a one-off export configured as a
standing source, or every later sync re-applies the same stale snapshot.

## Development

- Implementation: [`generic_csv.py`](generic_csv.py)
- Tests: [`test_generic_csv.py`](test_generic_csv.py)
- Plugin class: `CsvImportPlugin`
