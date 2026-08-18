# The StoryGraph (CSV Export)

Reads the CSV from **Manage Account → Manage Your Data → Export StoryGraph
Library**, which StoryGraph emails you. Books only.

## Columns read

`Title`, `Authors`, `Contributors`, `ISBN/UID`, `Format`, `Read Status`, `Date
Added`, `Last Date Read`, `Dates Read`, `Read Count`, `Moods`, `Pace`, the
character-attribute columns (`Character- or Plot-Driven?`, `Strong Character
Development?`, `Loveable Characters?`, `Diverse Characters?`, `Flawed
Characters?`), `Star Rating`, `Review`, `Content Warnings`, `Content Warning
Description`, `Tags` and `Owned?`.

A column the export no longer carries is fine — StoryGraph tweaks the shape over
time — as long as every row matches the header the file declares.

- Status: `read` → completed, `currently-reading` → currently consuming,
  `to-read` → unread, `did-not-finish` → completed (a rated-then-abandoned book
  is a real signal). Anything else → unread. The raw status is kept in
  `metadata["read_status"]`.
- Rating: quarter stars on a 0–5 scale, rounded half up and clamped to 1–5
  (`4.5` → 5, `3.25` → 3). A `0`, blank or unparseable rating is unrated.
- `ISBN/UID` becomes the item's external id.

## Skipped lines

Reported with the file line they were on:

- `no title`
- `N fields short of the header`
- `N fields more than the header`, usually an unquoted comma inside a value

## Development

- Implementation: [`storygraph_csv.py`](storygraph_csv.py)
- Tests: [`test_storygraph_csv.py`](test_storygraph_csv.py)
- Importer class: `StorygraphCsvImporter`, named `storygraph_csv`
