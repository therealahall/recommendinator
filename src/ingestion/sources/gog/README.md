# GOG

Imports owned games and (optionally) wishlist items from a [GOG.com](https://www.gog.com) library.

## Content type
- `video_game`

## Requirements
- GOG OAuth refresh token. The web UI Data tab can produce one via browser login; alternatively, follow the manual setup steps in `README.md`.

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
