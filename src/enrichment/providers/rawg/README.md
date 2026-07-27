# RAWG Enrichment Provider

Fills in metadata for video games using the [RAWG video game database](https://rawg.io/apidocs).

## Content types
- `video_game`

## Requirements
- A RAWG API key from https://rawg.io/apidocs.

## Configuration

Set these from the **Settings** page (Enrichment section), or the CLI:

```bash
python3.11 -m src.cli settings set enrichment.providers.rawg.enabled true

# The API key is a secret: hidden prompt, stored encrypted, never in a file
python3.11 -m src.cli settings set-secret enrichment.providers.rawg.api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `api_key` | str | yes (sensitive) | RAWG API key. |

## Behavior
- Resolves franchise membership and series ordering when RAWG has the data.
- Uses gap-filling — never overwrites existing fields.
- Outlier titles in fuzzy matches are filtered via longest-common-prefix heuristics.

## Development
- Implementation: [`rawg.py`](rawg.py)
- Tests: [`test_rawg.py`](test_rawg.py)
- Provider class: `RAWGProvider`
