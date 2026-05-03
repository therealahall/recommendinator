# Epic Games Store

Imports owned games from an Epic Games Store library via the Legendary library.

## Content type
- `video_game`

## Requirements
- Epic Games OAuth refresh token. Use the web UI Data tab to authenticate or follow the manual setup steps.

## Configuration

```yaml
inputs:
  epic_games:
    refresh_token: "YOUR_EPIC_REFRESH_TOKEN"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `refresh_token` | str | yes (sensitive) | Epic Games OAuth refresh token. |

## Notes
- Filters out non-base-game entries (DLC, add-ons) using the `is_base_game` heuristic.
- Items are imported as `unread`.

## Development
- Implementation: [`epic_games.py`](epic_games.py)
- Tests: [`test_epic_games.py`](test_epic_games.py)
- Plugin class: `EpicGamesPlugin`
