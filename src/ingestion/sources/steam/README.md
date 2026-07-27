# Steam

Imports owned games and playtime from a Steam library via the [Steam Web API](https://steamcommunity.com/dev).

## Content type
- `video_game`

## Requirements
- Steam Web API key — get one at https://steamcommunity.com/dev/apikey
- Either a Steam ID (64-bit) or a Steam vanity URL

## Configuration

Add the source from the **Data** tab with **+ Add source**, which prompts for the
API key at create time, or from the CLI:

```bash
python3.11 -m src.cli source create steam steam
python3.11 -m src.cli source set steam steam_id 76561198000000000  # OR vanity_url
python3.11 -m src.cli source set steam min_playtime_minutes 0      # Optional, default 0

# The API key is a secret: hidden prompt, stored encrypted, never in a file
python3.11 -m src.cli source set-secret steam api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `api_key` | str | yes (sensitive) | Steam Web API key |
| `steam_id` | str | one-of | Steam ID (64-bit). Required if `vanity_url` not provided. |
| `vanity_url` | str | one-of | Steam vanity URL. Resolved to `steam_id` automatically. |
| `min_playtime_minutes` | int | no | Minimum playtime to include (default `0`). |

## Notes
- Items are imported as `unread` — Steam does not expose a "completed" signal.
- Per-game metadata (genres, release date, etc.) is filled in by the RAWG enrichment provider so initial sync stays fast.

## Development
- Implementation: [`steam.py`](steam.py)
- Tests: [`test_steam.py`](test_steam.py)
- Plugin class: `SteamPlugin`
