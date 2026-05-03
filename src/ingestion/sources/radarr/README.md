# Radarr

Imports the movie library from a [Radarr](https://radarr.video) instance.

## Content type
- `movie`

## Requirements
- A reachable Radarr instance and an API key from Settings → General → Security.

## Configuration

```yaml
inputs:
  radarr:
    url: "http://localhost:7878"
    api_key: "YOUR_RADARR_API_KEY"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Radarr base URL (no trailing slash). |
| `api_key` | str | yes (sensitive) | Radarr API key. |

## Notes
- Items are imported as `unread` (Radarr tracks downloads, not consumption).
- Movies inside Radarr collections are tagged so the recommender can group them.
- Shares the [`ArrPlugin`](../arr_base.py) base class with Sonarr.

## Development
- Implementation: [`radarr.py`](radarr.py)
- Tests: [`test_radarr.py`](test_radarr.py)
- Plugin class: `RadarrPlugin`
