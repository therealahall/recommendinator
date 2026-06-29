# Generic JSON / JSONL Import

Imports content items from a JSON array or newline-delimited JSON file. Mirrors the field set of the generic CSV importer.

## Content types
- `book`, `movie`, `tv_show`, `video_game`

## Requirements
- A `.json` (array of objects) or `.jsonl` (one object per line) file.

## Importing

This is a one-shot file import, not a syncable `inputs:` source. Upload the file
from the web **Data** tab (**Import from file**) or run:

```bash
python3.11 -m src.cli import --source json_import --file /path/to/library.json --content-type book
```

| Option | Required | Description |
|--------|----------|-------------|
| `content_type` | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## Notes
- Field names match the [generic CSV](../generic_csv/README.md) plugin.
- File extension determines parsing mode: `.jsonl` → line-delimited; anything else → JSON array.

## Development
- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Plugin class: `JsonImportPlugin`
