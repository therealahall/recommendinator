# Wikidata Enrichment Provider

Places a work in the series [Wikidata](https://www.wikidata.org) states for it,
the one source naming one for all four content types.

## Content types
- `book`, `movie`, `tv_show`, `video_game`

## Requirements
- None — Wikidata is unauthenticated.

## Configuration

Set this from the **Settings** page (Enrichment section), or the CLI:

```bash
uv run python -m src.cli settings set enrichment.providers.wikidata.enabled true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |

## Behavior
- Supplies a series only, at `authored` authority, and never an item's match: it
  implements `fetch_series_ordinal` and not `enrich`, so the provider credited
  with an item stays the one that found its genres and description.
- Names the series from the English label of the `P179` entity, and positions the
  work in it from that statement's `P1545` qualifier. A statement carrying no
  readable ordinal still names the series, unpositioned, and `SeriesOrder` then
  ranks it by release year — counting a `P155`/`P156` chain would invent the rank
  the qualifier exists to state.
- Takes the narrowest of several `P179` series: one the title itself names is the
  work's own (Donkey Kong, not Mario), and past that the longer name is the
  sub-series (Mega Man X, not Mega Man). Its ordinal travels with it, so a
  franchise's number is never filed under a trilogy's name.
- Takes a search hit only where `P31` matches the item's content type and, where
  the item carries a year, the entity's own is within three. A wrong entity's
  series replaces every weaker source's, so an ambiguous search writes nothing.
- Identifies itself by name and repository in the `User-Agent` of every request,
  and asks for one item a second: Wikidata is donated infrastructure.

## Development
- Implementation: [`wikidata.py`](wikidata.py)
- Tests: [`test_wikidata.py`](test_wikidata.py)
- Provider class: `WikidataProvider`
