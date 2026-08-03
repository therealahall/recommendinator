# Generic CSV Import

Imports content items from a generic CSV file. Each input maps to a single content type; recognised columns vary by type.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import — set via the `content_type` config field)

## Requirements
- A CSV file with at minimum a `title` column for the configured content type.

## Configuration

```bash
python3.11 -m src.cli source create my_csv csv_import
python3.11 -m src.cli source set my_csv path /path/to/library.csv
python3.11 -m src.cli source set my_csv content_type book   # or movie, tv_show, video_game
```

Or add it from the **Data** tab with **+ Add source**. The id (`my_csv` above) is
yours to choose, so you can have several CSV sources for different files.

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

`ignored` is optional in two senses. Leave the column out entirely, or leave a
cell blank, and the import says nothing about the flag for those rows, so an item
you ignored in the app stays ignored across re-imports. Write a real value and it
wins in either direction — `false` un-ignores the item, which is what makes the
export-edit-re-import round trip usable. The blank case matters because a CSV
header applies to every row: without it, a file carrying the column would clear
the flag on every row it left empty.

That blank-cell rule protects a CSV you maintain by hand. **It protects nothing
about this app's own exports**, which write a real `true` or `false` into
`ignored` on every row, never a blank. Re-importing an export therefore replaces
the whole ignore list with the state it had when the export was taken, clearing
anything ignored since — deliberately, since that is the bulk un-ignore path, but
it makes an export a snapshot rather than a patch. Do not leave a one-off export
configured as a standing source: every later sync would re-apply the same stale
snapshot.

The other fields you own are not governed by one rule, so check the one you care
about: **rating** and **review** are filled only while the stored value is empty
and are never overwritten; **status** only moves forward (unread → consuming →
completed), so a file can advance a status but never revert a completion — except
that raising `total_seasons` above a completed show's watched-season list sends it
back to in-progress, which is a season rule rather than a status one; and
**`date_completed`** is replaced only by a later date.

The metadata columns divide three ways as well. `genre` is **additive** — an
imported genre joins the stored ones and never replaces them. `total_seasons` is
**monotonic** — it only increases. `isbn`, `pages`, `year_published`, `year`,
`runtime_minutes`, `platform`, `hours_played` and `notes` are **fill-only**: they
are written while the library has no value and ignored otherwise, so editing one
in an export and re-importing does nothing. `seasons_watched` is fill-only as
well: a stored list always wins, so the season checklist in the edit modal (or
`library edit --seasons-watched`) is the only way to *change* one.

`notes` is the fill-only column most likely to surprise you. It is universal
rather than type-specific, and it invites hand-editing in a way an ISBN does
not — but a stored note always wins, and there is no edit surface for it
anywhere in the app. Once a note is stored, nothing changes it: re-importing is
ignored, removing and re-adding the source drops that source's config and the
secrets its plugin currently declares sensitive while leaving its items in
place, and the app has no way to delete a library item.

`hours_played` is stored as the playtime the length scorer reads, so importing a
games CSV that carries it will change which games get recommended — see
[SCORING.md](../../../../docs/SCORING.md#content-length-preferences).

## Development
- Implementation: [`generic_csv.py`](generic_csv.py)
- Tests: [`test_generic_csv.py`](test_generic_csv.py)
- Plugin class: `CsvImportPlugin`
