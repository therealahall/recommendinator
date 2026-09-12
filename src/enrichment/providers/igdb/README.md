# IGDB Enrichment Provider

Fills a video game's genres, themes, description and cover, and names the series [IGDB](https://www.igdb.com) places it in.

## Content types
- `video_game`

## Requirements
- A Twitch application, from [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps): IGDB authenticates through Twitch. Register one, give it any OAuth redirect URL, and copy its client ID and client secret.

## Configuration

```bash
uv run python -m src.cli settings set enrichment.providers.igdb.enabled true
uv run python -m src.cli settings set-secret enrichment.providers.igdb.client_id
uv run python -m src.cli settings set-secret enrichment.providers.igdb.client_secret
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `client_id` | str | yes (sensitive) | Twitch application client ID. |
| `client_secret` | str | yes (sensitive) | Twitch application client secret. |

## Behavior
- Names the series from the game's collection, falling back to its franchise, and never states a position: no collection, franchise or membership record at IGDB holds one. A game in neither grouping gets nothing.
- Fills `genres`, `tags` from IGDB's themes, the summary as the description, an `https` cover and `release_year`. It searches under the shared cleaned title (`clean_game_title_for_search` in `src/utils/text.py`) and compares IGDB's alternative names too, so a store's capitalisation and trademark symbols still resolve.
- Mints a Twitch app access token on first use and holds it in memory for its stated lifetime. Nothing persists it — only the two credentials it is minted from are stored. A rejected token is minted once more and the call retried; a second rejection fails the item.
- Walks redirects itself and refuses one leaving `api.igdb.com`, since every request carries that token.

## Development
- Implementation: [`igdb.py`](igdb.py)
- Tests: [`test_igdb.py`](test_igdb.py)
- Provider class: `IGDBProvider`
