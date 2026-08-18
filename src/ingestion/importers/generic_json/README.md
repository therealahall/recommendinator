# Generic JSON / JSONL Import

Reads a JSON array of objects, or one object per line. The text decides which:
anything starting with `[` is an array, everything else is line-delimited.

## Fields

Names and per-field rules match the
[generic CSV importer](../generic_csv/README.md#recognized-columns), `ignored`
included: omit it or send `null` and the stored flag is left alone, send a real
`true` or `false` and it wins in either direction.

Unlike a CSV cell, a JSON field can hold a list directly, so `genre` and
`platform` accept either one value or an array.

A JSON file this app exported carries a real value on every entry, so
re-importing one replaces your whole ignore list with its state at export time.
[The same snapshot warning](../generic_csv/README.md#ignored) applies.

## Skipped entries

Reported by position in the array, or by line number in a JSONL file:

| Reason | What it means |
|---|---|
| `no title` | The `title` field was empty or absent. |
| `not a JSON object` | The entry is a bare string, number or list. |

Text that is not JSON at all is refused whole rather than entry by entry.

## Development

- Implementation: [`generic_json.py`](generic_json.py)
- Tests: [`test_generic_json.py`](test_generic_json.py)
- Importer class: `JsonImporter`, named `json_import`
