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
python3.11 -m src.cli source create radarr radarr
python3.11 -m src.cli source set radarr url http://localhost:7878

# The API key is a secret: hidden prompt, stored encrypted, never in a file
python3.11 -m src.cli source set-secret radarr api_key
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
