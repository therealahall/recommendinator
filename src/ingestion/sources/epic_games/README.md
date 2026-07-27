# Epic Games Store

Imports owned games from an Epic Games Store library via the Legendary library.

## Content type
- `video_game`

## Requirements
- Epic Games OAuth refresh token. Use the web UI Data tab to authenticate or follow the setup steps below. The token is stored in an encrypted credential database — not in `config.yaml`.

## Setup

Epic uses OAuth via the [Legendary](https://github.com/derrod/legendary) launcher's API client. Works in both local and Docker installs — no host-side tools needed.

1. Start the web server and open the **Data** tab, then click **+ Add source** and
   pick Epic Games. (Or from the CLI:
   `python3.11 -m src.cli source create epic_games epic_games`.)
2. Click **Connect Epic Games** — this opens Epic's login page in a new tab.
3. Log in with your Epic account — you'll see a JSON response containing an `authorizationCode`.
4. Copy the code (or the entire JSON), paste it into the web UI input, and click **Connect**. The token is encrypted and stored automatically.

If Epic sync later fails with an authentication error, the refresh token has expired — reconnect via the web UI. You can also connect from the CLI with `python3.11 -m src.cli auth connect --source epic`.

## Configuration

There is nothing to configure by hand. The connect flow above creates the source
and stores the refresh token encrypted; it is never entered in a file.

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
