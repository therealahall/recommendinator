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
uv run python -m src.cli source create sonarr sonarr
uv run python -m src.cli source set sonarr url http://localhost:8989

# The API key is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli source set-secret sonarr api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Sonarr base URL (no trailing slash). |
| `api_key` | str | yes (sensitive) | Sonarr API key. |
| `verify_ssl` | bool | no | Verify the TLS certificate (default `true`; set `false` for a private CA). |

Moving Sonarr to another host takes the steps in
[SECURITY.md](../../../../docs/SECURITY.md#credential-encryption).

## Notes
- Items are imported as `unread` (Sonarr tracks downloads, not consumption).
- Per-season episode counts and status are extracted from the API response.
- Shares the [`ArrPlugin`](../arr_base.py) base class with Radarr.

## Development
- Implementation: [`sonarr.py`](sonarr.py)
- Tests: [`test_sonarr.py`](test_sonarr.py)
- Plugin class: `SonarrPlugin`
