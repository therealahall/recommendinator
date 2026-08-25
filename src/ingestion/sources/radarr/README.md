# Radarr

Imports the movie library from a [Radarr](https://radarr.video) instance.

## Content type
- `movie`

## Requirements
- A reachable Radarr instance and an API key from Settings → General → Security.

## Configuration

Add the source from the **Data** tab with **+ Add source**, which prompts for the
API key at create time, or from the CLI:

```bash
uv run python -m src.cli source create radarr radarr
uv run python -m src.cli source set radarr url http://localhost:7878

# The API key is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli source set-secret radarr api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Radarr base URL (no trailing slash). |
| `api_key` | str | yes (sensitive) | Radarr API key. |
| `verify_ssl` | bool | no | Verify the TLS certificate (default `true`; set `false` for a private CA). |

Moving Radarr to another host takes the steps in
[SECURITY.md](../../../../docs/SECURITY.md#credential-encryption).

## Notes
- Items are imported as `unread` (Radarr tracks downloads, not consumption).
- A redirect that leaves the configured origin is refused, not followed: a
  reverse proxy bouncing `http` to `https` is reported as the scheme change it
  is, and the API key never reaches a host `url` does not name.
- Movies inside Radarr collections are tagged so the recommender can group them.
- Shares the [`ArrPlugin`](../arr_base.py) base class with Sonarr.

## Development
- Implementation: [`radarr.py`](radarr.py)
- Tests: [`test_radarr.py`](test_radarr.py)
- Plugin class: `RadarrPlugin`
