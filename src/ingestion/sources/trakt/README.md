# Trakt

Imports watched movies and TV shows, ratings, and (optionally) the watchlist from a [Trakt](https://trakt.tv) account.

## Content types
- `tv_show`
- `movie`

## Requirements
- Your **own Trakt API application**. Register one at [https://trakt.tv/oauth/applications](https://trakt.tv/oauth/applications) to obtain a `client_id` and `client_secret`. There is no shared app: Trakt enforces per-application rate limits and Terms of Service, so each user authenticates against their own registered application.
- A `refresh_token`, obtained later via the device-code OAuth flow (see Setup). You do not create this by hand.

The `client_secret` and `refresh_token` are stored in an encrypted credential database — never in `config.yaml`. Only the non-sensitive `client_id` lives in YAML.

When registering the application, you can use `urn:ietf:wg:oauth:2.0:oob` as the redirect URI; the device-code flow does not rely on a browser redirect back to your app.

## Setup

First, save your `client_id` and `client_secret` so the connect flow can run, then complete the device-code authorization through the web UI or the CLI.

### Option 1: Web UI (recommended)

1. Enable Trakt in `config.yaml` with your application's client id:
   ```yaml
   inputs:
     trakt:
       plugin: trakt
       client_id: "YOUR_TRAKT_CLIENT_ID"
       enabled: true
   ```
2. Start the web server and open the **Data** tab.
3. In the Trakt source panel, add your `client_secret` using the **Replace** action (it is stored encrypted).
4. Click **Connect Trakt Account**.
5. Go to the verification URL shown (e.g. `https://trakt.tv/activate`) and enter the displayed code.
6. The app polls Trakt until you approve, then stores the `refresh_token` encrypted automatically.

### Option 2: CLI

After the `client_id` and `client_secret` are saved (via the web UI panel or a migrated config entry), run the device-code flow:

```bash
# Start the Trakt device-code flow — prints a verification URL and code,
# then polls until you approve the request on Trakt.
python3.11 -m src.cli auth connect --source trakt

# Check connection status for all OAuth sources
python3.11 -m src.cli auth status

# Disconnect and remove the stored refresh token
python3.11 -m src.cli auth disconnect --source trakt
```

`auth connect --source trakt` prints `Go to <verification_url> and enter code: <user_code>`, then polls at the cadence Trakt returns until you approve, the code expires, or the request is denied.

If Trakt sync later fails with an authentication error, reconnect via the web UI or `auth connect --source trakt` — Trakt rotates the refresh token on every sync, and the plugin persists the new value automatically.

## Configuration

```yaml
inputs:
  trakt:
    plugin: trakt
    client_id: "YOUR_TRAKT_CLIENT_ID"
    include_watchlist: true     # Optional, default true
    enabled: false
```

Trakt imports both movies and TV shows in a single sync (the plugin sets each item's type itself), so there is no `content_type` key. `client_secret` and `refresh_token` are entered via the connect flow and stored encrypted — never in YAML.

| Field | Type | Required | Sensitive | Description |
|-------|------|----------|-----------|-------------|
| `client_id` | str | yes | no | Your Trakt API application client id. Sent as the `trakt-api-key` header on every request. |
| `client_secret` | str | yes | yes | Your Trakt API application client secret. Entered via the connect flow and stored encrypted — not placed in YAML. |
| `refresh_token` | str | yes | yes | Trakt OAuth refresh token, obtained via the device-code flow and stored encrypted — not placed in YAML. |
| `include_watchlist` | bool | no | no | Import watchlisted titles as `unread` items (default `true`). |

## Notes
- **Watched history** is imported as completion state:
  - Movies in your watched history become `completed`.
  - Shows become `completed` when every aired episode has been watched, otherwise `currently_consuming`. Season progress is tracked in metadata via `seasons_watched` (the watched season numbers) and `total_seasons`. Season 0 (specials) is excluded. For in-progress shows, `total_seasons` is the show's true real-season count, fetched with one extra `GET /shows/{id}/seasons` call per in-progress show, so the recommender can surface the user's unwatched later seasons. For completed shows it is the highest watched season (no extra call is made).
- **Ratings** are normalized from Trakt's 1–10 scale to the project's 1–5 scale (halved and rounded up, so a rated item never normalizes to 0). Unrated items (0 or absent) stay unrated.
- **Watchlist** items are imported as `unread`. Toggle this with `include_watchlist`.
- The same title across watched/ratings/watchlist lists merges into a single item — watched status takes priority over watchlist, and ratings attach to whichever entry exists.
- The OAuth refresh token is rotated by Trakt on each sync; the plugin persists the new value via the credential storage callback.

## Development
- Implementation: [`trakt.py`](trakt.py)
- Tests: [`test_trakt.py`](test_trakt.py)
- Plugin class: `TraktPlugin`
