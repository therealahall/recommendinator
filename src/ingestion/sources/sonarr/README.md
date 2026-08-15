# Sonarr

Imports the TV series library from a [Sonarr](https://sonarr.tv) instance.

## Content type
- `tv_show`

## Requirements
- A reachable Sonarr instance and an API key from Settings → General → Security.

## Configuration

Add the source from the **Data** tab with **+ Add source**, which prompts for the
API key at create time, or from the CLI:

```bash
python3.11 -m src.cli source create sonarr sonarr
python3.11 -m src.cli source set sonarr url http://localhost:8989

# The API key is a secret: hidden prompt, stored encrypted, never in a file
python3.11 -m src.cli source set-secret sonarr api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Sonarr base URL (no trailing slash). |
| `api_key` | str | yes (sensitive) | Sonarr API key. |
| `verify_ssl` | bool | no | Verify the TLS certificate (default `true`; set `false` for a private CA). |

**Changing `url` clears the stored API key**, so it is never sent to a host it
was not issued for. Re-run `source set-secret` after moving Sonarr.

## Notes
- Items are imported as `unread` (Sonarr tracks downloads, not consumption).
- A redirect that leaves the configured origin is refused, not followed: a
  reverse proxy bouncing `http` to `https` is reported as the scheme change it
  is, and the API key never reaches a host `url` does not name.
- Per-season episode counts and status are extracted from the API response.
- Shares the [`ArrPlugin`](../arr_base.py) base class with Radarr.

## Development
- Implementation: [`sonarr.py`](sonarr.py)
- Tests: [`test_sonarr.py`](test_sonarr.py)
- Plugin class: `SonarrPlugin`
