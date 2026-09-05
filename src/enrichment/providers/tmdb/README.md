# TMDB Enrichment Provider

Fills in metadata for movies and TV shows using [The Movie Database (TMDB)](https://www.themoviedb.org).

## Content types
- `movie`, `tv_show`

## Requirements
- A TMDB API key (v3) from https://www.themoviedb.org/settings/api.

## Configuration

Set these from the **Settings** page (Enrichment section), or the CLI:

```bash
uv run python -m src.cli settings set enrichment.providers.tmdb.enabled true
uv run python -m src.cli settings set enrichment.providers.tmdb.language en-US        # Optional, default "en-US"
uv run python -m src.cli settings set enrichment.providers.tmdb.include_keywords true # Optional, default true

# The API key is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli settings set-secret enrichment.providers.tmdb.api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `api_key` | str | yes (sensitive) | TMDB v3 API key. |
| `language` | str | no | Language for results (e.g. `en-US`, `de-DE`). Default `en-US`. |
| `include_keywords` | bool | no | Fetch the keyword set for tag enrichment (default `true`). |

## Behavior
- Searches by title, with year-aware disambiguation when available. A search hit
  is only taken when one of its titles — the one in the configured language or
  the original — is near-identical and, where the item carries a
  year, its release lands within three years; anything else settles as not found
  rather than storing the wrong film. An item carrying a TMDB id skips the search
  and the check.
- Uses gap-filling — never overwrites existing fields.
- Rate-limited to TMDB's 40 requests/sec ceiling.
- Enriches genres, description, tags (keywords), and extra metadata. For movies
  this includes runtime, ratings, release date/year, language, studio, series
  ordering, and `director` (from credits, up to 3 directors comma-joined). For
  TV shows it includes seasons, episodes, networks, status, and `creators` (up
  to 3 creators comma-joined).

## Development
- Implementation: [`tmdb.py`](tmdb.py)
- Tests: [`test_tmdb.py`](test_tmdb.py)
- Provider class: `TMDBProvider`
