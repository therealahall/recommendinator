# The StoryGraph (CSV Export)

Imports books from a The StoryGraph library CSV export.

## Content type
- `book`

## Requirements
- A The StoryGraph library CSV export. The StoryGraph has no public API, so
  generate the file from your account: **Manage Account → Manage Your Data →
  Export StoryGraph Library**. StoryGraph emails you the CSV.

## Configuration

```yaml
inputs:
  storygraph_csv:
    plugin: storygraph_csv
    path: "/path/to/storygraph_export.csv"
    content_type: "book"
    enabled: true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to The StoryGraph library CSV export file. |

## Notes
- No API key or network access required — pure file import.
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
