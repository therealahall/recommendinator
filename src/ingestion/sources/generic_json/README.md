# Generic JSON / JSONL Import

Imports content items from a JSON array or newline-delimited JSON file. Mirrors the field set of the generic CSV importer.

## Content types
- `book`, `movie`, `tv_show`, `video_game`

## Requirements
- A `.json` (array of objects) or `.jsonl` (one object per line) file.

## Configuration

```yaml
inputs:
  json_import:
    path: "/path/to/library.json"
    content_type: "book"   # or movie, tv_show, video_game
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the JSON or JSONL file. |
| `content_type` | str | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## Notes
- Field names match the [generic CSV](../generic_csv/README.md) plugin.
- File extension determines parsing mode: `.jsonl` → line-delimited; anything else → JSON array.

## Development
- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Plugin class: `JsonImportPlugin`
