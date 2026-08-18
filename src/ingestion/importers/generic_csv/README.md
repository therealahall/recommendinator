# Generic CSV Import

Reads a CSV of one content type, which you pick when you upload the file.

## Recognized columns

- Universal: `title`, `status`, `rating`, `date_completed`, `review`, `notes`,
  `ignored`
- `book`: `author`, `isbn`, `pages`, `year_published`, `genre`
- `movie`: `director`, `year`, `runtime_minutes`, `genre`
- `tv_show`: `creator`, `seasons_watched`, `total_seasons`, `year`, `genre`
- `video_game`: `developer`, `platform`, `genre`, `hours_played`

`title` is required and a file without it is refused whole. Unknown columns are
logged and ignored.

`status` takes type-specific aliases: `read`/`watched`/`played` for completed,
`reading`/`watching`/`playing` for currently consuming, and
`to_read`/`to_watch`/`to_play`/`wishlist`/`unwatched`/`unplayed` for unread.
Boolean fields accept `true`/`false`, `1`/`0` and `yes`/`no`. `seasons_watched`
takes a list, `1,2,5,6`, or a count integer.

## Skipped rows

Reported with the file line they were on, so a spreadsheet's own row numbers
match:

| Reason | What it means |
|---|---|
| `no title` | The `title` cell was empty. |
| `N fields short of the header` | The row has fewer fields than the header. |

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

### `ignored`

Leave the column out, or a cell blank, and the import says nothing about the
flag, so an item you ignored in the app stays ignored. Write a real value and it
wins in either direction, `false` un-ignoring. The blank case matters because a
CSV header applies to every row: without it, a file carrying the column would
clear the flag on every row it left empty.

**That protects a CSV you maintain by hand. It protects nothing about this app's
own exports**, which write a real `true` or `false` on every row. Re-importing
one replaces your whole ignore list with its state at export time, clearing
anything ignored since. That makes an export a snapshot rather than a patch.

### A cell opening with `=`, `+`, `-` or `@`

A spreadsheet reads one as a formula, and a title or genre can be text a
metadata provider supplied, so the export writes the cell behind an apostrophe:
`=1+1` becomes `'=1+1`. A tab or a carriage return is guarded the same way.

The import strips that apostrophe off any cell with a formula character right
behind it, whoever wrote the file. The round trip is therefore not quite
lossless: a title that really is `'=1+1` exports unchanged and comes back as
`=1+1`, and a genre of `'-ish` in a CSV from elsewhere is stored as `-ish`. The
two are the same bytes, so nothing can tell them apart. No spreadsheet sees
either unguarded, because the next export puts the apostrophe back.

An apostrophe with anything else behind it is left alone.

## Development

- Implementation: [`generic_csv.py`](generic_csv.py)
- Tests: [`test_generic_csv.py`](test_generic_csv.py)
- Importer class: `CsvImporter`, named `csv_import`
