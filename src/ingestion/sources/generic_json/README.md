# Generic JSON / JSONL Import

Imports content items from a JSON array or newline-delimited JSON file. Mirrors the field set of the generic CSV importer. This is a **one-shot file import**, not a source: the file is read once and nothing about it is stored, so import each file whenever you want its contents in your library.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import, set via the `content_type` option)

## Requirements
- A `.json` (array of objects) or `.jsonl` (one object per line) file.
- UTF-8 encoded. A file saved with a byte-order mark works as is.

## Import

Open the **Data** tab, click **Import from file**, pick **JSON Import**, choose the
file, and select a content type. Or from the CLI:

```bash
python3.11 -m src.cli import --source json_import --file /path/to/library.json --content-type book
```

Import one file per content type. Web uploads are capped at 50 MB, and the CLI has
no cap.

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `content_type` | str | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## Notes
- Field names match the [generic CSV](../generic_csv/README.md) plugin.
- The parser detects the format from the content, not the file name: a file whose first non-whitespace character is `[` is read as a JSON array, anything else as one JSON object per line. So a `.jsonl` file parses identically whichever interface it arrives through, and renaming an export does not change how it is read. The extension only picks the label in the log line and the filter on the upload picker, which accepts both `.json` and `.jsonl`.

## Development
- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Plugin class: `JsonImportPlugin`
