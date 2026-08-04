# Generic JSON / JSONL Import

Imports content items from a JSON array or a newline-delimited JSON file. Mirrors
the field set of the generic CSV importer.

## Requirements

A `.json` (array of objects) or `.jsonl` (one object per line) file. The
extension picks the parsing mode: `.jsonl` is line-delimited, anything else is a
JSON array.

## Configuration

```bash
python3.11 -m src.cli source create my_json json_import
python3.11 -m src.cli source set my_json path /path/to/library.json
python3.11 -m src.cli source set my_json content_type book
```

Or use **+ Add source** on the **Data** tab. The id (`my_json`) is yours to pick.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the JSON or JSONL file. |
| `content_type` | str | yes | One of `book`, `movie`, `tv_show`, `video_game`. |

## Fields

Names and per-field rules match the
[generic CSV importer](../generic_csv/README.md#recognized-columns), `ignored`
included: omit it or send `null` and the stored flag is left alone, send a real
`true` or `false` and it wins in either direction.

A JSON file this app exported carries a real value on every entry, so
re-importing one replaces your whole ignore list with its state at export time.
[The same snapshot warning](../generic_csv/README.md#ignored) applies.

## Development

- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Plugin class: `JsonImportPlugin`
