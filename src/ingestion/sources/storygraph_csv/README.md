# The StoryGraph (CSV Export)

Imports books from a The StoryGraph library CSV export. This is a **one-shot file
import**, not a source: there is nothing to create, configure, or re-sync. To
pick up new books, export again and import again.

## Content type
- `book`

## Requirements
- A The StoryGraph library CSV export. The StoryGraph has no public API, so
  generate the file from your account: **Manage Account → Manage Your Data →
  Export StoryGraph Library**. StoryGraph emails you the CSV.

## Import

Open the **Data** tab, click **Import from file**, pick **The StoryGraph (CSV
Export)**, and choose your export. Or from the CLI:

```bash
python3.11 -m src.cli import --source storygraph_csv --file /path/to/storygraph_export.csv
```

This plugin takes no import options, since it is always books. Web uploads are
capped at 50 MB, and the CLI has no cap.

## Notes
- No API key or network access required.
- An export re-saved in Excel (UTF-8 with a byte-order mark) imports as is.
- Reads `Title`, `Authors`, `Contributors`, `ISBN/UID`, `Format`, `Read Status`,
  `Date Added`, `Last Date Read`, `Dates Read`, `Read Count`, `Moods`, `Pace`,
  the character-attribute columns (`Character- or Plot-Driven?`, `Strong
  Character Development?`, `Loveable Characters?`, `Diverse Characters?`,
  `Flawed Characters?`), `Star Rating`, `Review`, `Content Warnings`, `Content
  Warning Description`, `Tags`, and `Owned?`. Missing or extra columns are
  tolerated — StoryGraph tweaks the export shape over time.
- Status mapping: `read` → completed, `currently-reading` → currently consuming,
  `to-read` → unread, `did-not-finish` → completed (a rated-then-abandoned book
  is a real signal). Anything else → unread. The raw status is kept in
  `metadata["read_status"]`.
- Rating mapping: StoryGraph rates in quarter-star steps on a 0–5 scale. Ratings
  are rounded half up and clamped to 1–5 (e.g. `4.5` → 5, `3.25` → 3, `3.75` →
  4). A `0`, blank, or unparseable rating is treated as unrated.

## Development
- Implementation: [`storygraph_csv.py`](storygraph_csv.py)
- Tests: [`test_storygraph_csv.py`](test_storygraph_csv.py)
- Plugin class: `StorygraphCsvPlugin`
