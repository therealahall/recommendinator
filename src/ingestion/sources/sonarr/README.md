# Sonarr

Imports the TV series library from a [Sonarr](https://sonarr.tv) instance.

## Content type
- `tv_show`

## Requirements
- A reachable Sonarr instance and an API key from Settings → General → Security.

## Configuration

```yaml
inputs:
  sonarr:
    url: "http://localhost:8989"
    api_key: "YOUR_SONARR_API_KEY"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Sonarr base URL (no trailing slash). |
| `api_key` | str | yes (sensitive) | Sonarr API key. |

## Notes
- Items are imported as `unread` (Sonarr tracks downloads, not consumption).
- Per-season episode counts and status are extracted from the API response.
- Shares the [`ArrPlugin`](../arr_base.py) base class with Radarr.

## Development
- Implementation: [`sonarr.py`](sonarr.py)
- Tests: [`test_sonarr.py`](test_sonarr.py)
- Plugin class: `SonarrPlugin`
