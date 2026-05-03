# TMDB Enrichment Provider

Fills in metadata for movies and TV shows using [The Movie Database (TMDB)](https://www.themoviedb.org).

## Content types
- `movie`, `tv_show`

## Requirements
- A TMDB API key (v3) from https://www.themoviedb.org/settings/api.

## Configuration

```yaml
enrichment:
  providers:
    tmdb:
      enabled: true
      api_key: "YOUR_TMDB_API_KEY"
      language: "en-US"        # Optional, default "en-US"
      include_keywords: true   # Optional, default true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `api_key` | str | yes (sensitive) | TMDB v3 API key. |
| `language` | str | no | Language for results (e.g. `en-US`, `de-DE`). Default `en-US`. |
| `include_keywords` | bool | no | Fetch the keyword set for tag enrichment (default `true`). |

## Behavior
- Searches by title, with year-aware disambiguation when available.
- Uses gap-filling — never overwrites existing fields.
- Rate-limited to TMDB's 40 requests/sec ceiling.

## Development
- Implementation: [`tmdb.py`](tmdb.py)
- Tests: [`test_tmdb.py`](test_tmdb.py)
- Provider class: `TMDBProvider`
