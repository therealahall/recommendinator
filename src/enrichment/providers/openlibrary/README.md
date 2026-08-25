# OpenLibrary Enrichment Provider

Fills in metadata for books using the [OpenLibrary](https://openlibrary.org) public API.

## Content types
- `book`

## Requirements
- None — OpenLibrary is unauthenticated.

## Configuration

Set this from the **Settings** page (Enrichment section), or the CLI:

```bash
uv run python -m src.cli settings set enrichment.providers.openlibrary.enabled true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |

## Behavior
- Searches by title (and author when available) with series-suffix cleanup applied to improve match quality.
- Uses gap-filling — never overwrites existing fields.

## Development
- Implementation: [`openlibrary.py`](openlibrary.py)
- Tests: [`test_openlibrary.py`](test_openlibrary.py)
- Provider class: `OpenLibraryProvider`
