# Tautulli

Imports watched movies and TV episodes from the Plex history a [Tautulli](https://tautulli.com) instance records.

## Content types
- `movie`
- `tv_show`

## Requirements
- A reachable Tautulli instance and an API key from Settings > Web Interface > API.
- The Plex username whose history to import. Case does not have to match the one Plex holds.

## Configuration

Add the source from the **Data** tab with **+ Add source**, which prompts for the
API key at create time, or from the CLI:

```bash
uv run python -m src.cli source create tautulli tautulli
uv run python -m src.cli source set tautulli url http://localhost:8181
uv run python -m src.cli source set tautulli username <plex-username>

# The API key is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli source set-secret tautulli api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Tautulli base URL (default `http://localhost:8181`). |
| `api_key` | str | yes (sensitive) | Tautulli API key. |
| `username` | str | yes | The Plex username whose watch history to import. |
| `verify_ssl` | bool | no | Verify the TLS certificate (default `true`; set `false` for a private CA). |

Moving Tautulli to another host takes the steps in
[SECURITY.md](../../../../docs/SECURITY.md#credential-encryption).

## When a season counts as watched

A season is finished only when every episode of it has been watched, counted against the best episode count available for that season.

With Sonarr or TMDB configured, that is the number of episodes that have aired. Without either, it is the number of episodes Plex holds, which finishes a season early on an incomplete library. With neither, the season is never ticked and nothing is guessed.

**A show no longer in the Plex library imports with no seasons ticked.** Its watch history still arrives, but there is no season size left to measure against.

## Notes
- Tautulli records plays and never ratings, so imported items carry none.
- Only plays Tautulli itself marked watched are counted, at whatever threshold that instance is configured with.
- History belonging to other Plex users on the server is not imported.
- Movies arrive `completed`, dated the local day of their latest play. Shows arrive in progress, and the season tally above is what finishes them.
- Season 0, where Plex files specials, is excluded from every count.

## Development
- Implementation: [`tautulli.py`](tautulli.py)
- Tests: [`test_tautulli.py`](test_tautulli.py)
- Plugin class: `TautulliPlugin`
