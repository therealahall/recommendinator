# Generic JSON / JSONL Import

Imports content items from a JSON array or newline-delimited JSON file. Mirrors the field set of the generic CSV importer.

## Content types
- `book`, `movie`, `tv_show`, `video_game`

## Requirements
- A `.json` (array of objects) or `.jsonl` (one object per line) file.

## Configuration

```bash
python3.11 -m src.cli source create my_json json_import
python3.11 -m src.cli source set my_json path /path/to/library.json
python3.11 -m src.cli source set my_json content_type book   # or movie, tv_show, video_game
```

Or add it from the **Data** tab with **+ Add source**. The id (`my_json` above) is
yours to choose, so you can have several JSON sources for different files.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the JSON or JSONL file. |
| `content_type` | str | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## Notes
- Field names match the [generic CSV](../generic_csv/README.md) plugin, including the optional `ignored` field: omit it, or send `null`, and the import leaves the stored flag alone; send a real `true`/`false` and its value wins in either direction. A JSON file this app exported always carries a real `true`/`false` on every entry, so re-importing one replaces the whole ignore list with the state it had at export time — [the same snapshot warning](../generic_csv/README.md#recognized-columns) that applies to CSV exports.
- The rest of the fields you own follow the same per-field rules as the CSV importer — rating and review are fill-only, status moves forward only (bar the completed-TV-show-gains-a-season case), `date_completed` is replaced only by a later date, `genre` is additive, `total_seasons` only increases, and every remaining metadata field, `seasons_watched` included, is fill-only. See [that README](../generic_csv/README.md#recognized-columns) for the detail.
- File extension determines parsing mode: `.jsonl` → line-delimited; anything else → JSON array.

## Development
- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Plugin class: `JsonImportPlugin`
