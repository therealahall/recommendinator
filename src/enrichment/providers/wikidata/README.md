# Wikidata Enrichment Provider

Positions a work in its series from [Wikidata](https://www.wikidata.org), the
one source stating an ordinal for all four content types.

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
- Supplies a series position only, at `authored` authority, and never an item's
  match: it implements `fetch_series_ordinal` and not `enrich`, so the provider
  credited with an item stays the one that found its genres and description.
- Takes the position from the `P1545` qualifier on a `P179` statement, and names
  the series from that entity's English label. A series stating no ordinal yields
  none — counting a `P155`/`P156` chain would invent the rank the qualifier
  exists to state.
- Refuses a work `P179` positions in more than one series, whether or not the
  two agree on the number: only one of them is the series the stored name means,
  and nothing in the statements says which.
- Takes a search hit only where `P31` matches the item's content type and, where
  the item carries a year, the entity's own is within three. A wrong entity's
  ordinal replaces every weaker source's, so an ambiguous search writes nothing.
- Identifies itself by name and repository in the `User-Agent` of every request,
  and asks for one item a second: Wikidata is donated infrastructure.

## Development
- Implementation: [`wikidata.py`](wikidata.py)
- Tests: [`test_wikidata.py`](test_wikidata.py)
- Provider class: `WikidataProvider`
