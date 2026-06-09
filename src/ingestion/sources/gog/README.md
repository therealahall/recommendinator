# GOG

Imports owned games and (optionally) wishlist items from a [GOG.com](https://www.gog.com) library.

## Content type
- `video_game`

## Requirements
- GOG OAuth refresh token. The web UI Data tab can produce one via browser login; alternatively, follow the manual setup steps below. The token is stored in an encrypted credential database — not in `config.yaml`.

## Setup

### Option 1: Web UI (recommended)

1. Enable GOG in `config.yaml`:
   ```yaml
   inputs:
     gog:
       plugin: gog
       enabled: true
   ```
2. Start the web server and open the **Data** tab.
3. Follow the **Connect GOG Account** wizard — it runs the OAuth flow and stores the token securely.

### Option 2: Manual

1. Open the GOG auth URL in your browser:
   ```
   https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2
   ```
2. Log in with your GOG account.
3. After login you are redirected to a URL like:
   ```
   https://embed.gog.com/on_login_success?origin=client&code=LONG_CODE_HERE
   ```
   Copy the entire URL (or just the value after `code=`).
4. Paste the URL/code into the web UI to complete the connection. The token is encrypted and stored automatically.

If GOG sync later fails with an authentication error, the refresh token has expired — reconnect via the web UI. You can also connect from the CLI with `python3.11 -m src.cli auth connect --source gog`.

## Configuration

```yaml
inputs:
  gog:
    refresh_token: "YOUR_GOG_REFRESH_TOKEN"
    include_wishlist: true     # Optional, default true
    enrich_wishlist: true      # Optional, default true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `refresh_token` | str | yes (sensitive) | GOG OAuth refresh token. |
| `include_wishlist` | bool | no | Import wishlisted games as `unread` (default `true`). |
| `enrich_wishlist` | bool | no | Fetch detailed metadata for wishlist items (default `true`). |

## Notes
- The OAuth refresh token is rotated by GOG on each sync; the plugin persists the new value via the credential storage callback.
- Owned games are deduplicated against the wishlist so a single product appears at most once.

## Development
- Implementation: [`gog.py`](gog.py)
- Tests: [`test_gog.py`](test_gog.py)
- Plugin class: `GogPlugin`
