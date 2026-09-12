# RAWG Enrichment Provider

Fills in metadata for video games using the [RAWG video game database](https://rawg.io/apidocs).

## Content types
- `video_game`

## Requirements
- A RAWG API key from https://rawg.io/apidocs.

## Configuration

Set these from the **Settings** page (Enrichment section), or the CLI:

```bash
uv run python -m src.cli settings set enrichment.providers.rawg.enabled true

# The API key is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli settings set-secret enrichment.providers.rawg.api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `api_key` | str | yes (sensitive) | RAWG API key. |

## Behavior
- Writes `average_playtime_hours`, the average playtime RAWG reports, which is
  the only figure the
  [length scorer](../../../../docs/SCORING.md#content-length-preferences) reads
  for a game.
- A search hit is only taken when its name is near-identical and, where the item
  carries a year, its release lands within three years; anything else settles as
  not found rather than storing the wrong game.
- Says nothing about series. RAWG's `game-series` endpoint states membership
  without naming or ordering it, and both readings of that membership were
  wrong: a name guessed from the members called the 1941/1942/1943 shooters
  "194", and grouping on the set itself merged series that shared one member.
  Wikidata and IGDB state a name instead.
- Gap-fills every field it writes.

## Development
- Implementation: [`rawg.py`](rawg.py)
- Tests: [`test_rawg.py`](test_rawg.py)
- Provider class: `RAWGProvider`
