# Data Sources

Recommendinator imports your library from multiple sources through a plugin
architecture. Each source has its own setup guide; the table below links to it.
This document covers the parts that are shared across every source: managing
sources in the UI/CLI, parallel sync, and library export.

## Available sources

| Source | Type | Setup guide |
|--------|------|-------------|
| **Goodreads** | Books | [goodreads](../src/ingestion/sources/goodreads/README.md) — CSV export from your Goodreads library |
| **The StoryGraph** | Books | [storygraph_csv](../src/ingestion/sources/storygraph_csv/README.md) — CSV export from your StoryGraph library |
| **Steam** | Games | [steam](../src/ingestion/sources/steam/README.md) — automatic import via Steam Web API |
| **GOG** | Games | [gog](../src/ingestion/sources/gog/README.md) — OAuth; imports library and wishlist |
| **Epic Games** | Games | [epic_games](../src/ingestion/sources/epic_games/README.md) — OAuth via Legendary |
| **Sonarr** | TV Shows | [sonarr](../src/ingestion/sources/sonarr/README.md) — import from Sonarr API |
| **Radarr** | Movies | [radarr](../src/ingestion/sources/radarr/README.md) — import from Radarr API |
| **Trakt** | TV Shows / Movies | [trakt](../src/ingestion/sources/trakt/README.md) — OAuth device-code; imports watched history, ratings, and watchlist |
| **ROM Library** | Games | [roms](../src/ingestion/sources/roms/README.md) — scan emulator ROM directories |
| **CSV** | Any | [generic_csv](../src/ingestion/sources/generic_csv/README.md) — generic CSV with customizable mapping |
| **JSON** | Any | [generic_json](../src/ingestion/sources/generic_json/README.md) — generic JSON/JSONL import |
| **Markdown** | Any | [markdown](../src/ingestion/sources/markdown/README.md) — human-readable markdown lists |

Import file examples live in the `templates/` directory. Templates support the
`ignored` field for excluding items from recommendations, and TV show templates
use a `seasons_watched` list (e.g., `1,2,5,6` in CSV or `[1,2,5,6]` in JSON) to
track specific seasons watched.

## Adding, editing, and removing sources in the UI

The **Data** tab renders every configured source as an accordion. Both enabled
and disabled sources are shown — disabled accordions appear muted with a
"Disabled" badge and a non-actionable Sync button. Sources are sorted
enabled-first.

There are two ways to create a source:

- Click **+ Add source** at the top of the Sync Sources card. Pick a plugin from
  the dropdown, give the source an id, fill in any non-sensitive fields the
  plugin's schema declares, and click Create. The source goes straight into the
  database — no YAML edit required. Add sensitive fields (API keys, OAuth tokens)
  afterwards using the Replace action in the source's expanded panel.
- Define the source under `inputs:` in `config.yaml`, then click **Migrate to DB**
  in the source's expanded panel to copy the YAML entry into the database. After
  migration the YAML entry is ignored — all edits go through the UI.

Once a source is in the database, every field defined in its plugin's config
schema is editable inline from the web UI or via the
`python3.11 -m src.cli source` CLI commands. The exact set of fields differs per
plugin (e.g. Steam exposes `api_key` and `vanity_url`; Goodreads exposes `path`);
the generic CSV / JSON / Markdown plugins also expose `content_type`. Run
`python3.11 -m src.cli source schema <id>` to see what is editable for a given
source.

Each source has an Enable/Disable toggle in its action row. Disabled sources stay
in the list but are skipped during sync — `Sync All` and the per-source Sync
button both ignore them. Use the Remove button to drop a DB-backed source
entirely (clears every stored secret for that source).

Sensitive fields are stored encrypted and never returned by the API; the UI shows
a "set" / "unset" badge with **Replace** and **Clear** actions.

The same operations are available from the CLI `source` command group — see
[CLI.md](CLI.md#source-management) for the full reference.

## Parallel sync

When syncing multiple sources, each runs on its own worker thread, so the total
sync time is bounded by the slowest source rather than the sum of all sources.
Independent sources (e.g. GOG and Radarr) sync simultaneously since they hit
different APIs. Configure the worker pool in `config.yaml`:

```yaml
sync:
  max_workers: 4  # default; set to 1 for sequential
```

The CLI accepts `--workers N` to override per-invocation, e.g.
`python3.11 -m src.cli update --workers 8`. Per-source rate limits (e.g. GOG's
`rate_limit_seconds`) are enforced inside each plugin and remain untouched.

## Library export

The **Library** tab can be filtered by content type, consumption status, and
enrichment state (all items, enriched, or not enriched). The enrichment filter is
handy for finding items still missing metadata so you can edit them by hand — see
[ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md#manual-enrichment-editing).

Export your library data from the web UI:

1. Go to the **Library** tab.
2. Select a content type from the type filter.
3. Choose a format (CSV or JSON).
4. Click **Export** to download.

Exported files match the import template format, so you can edit them (e.g., mark
items as `ignored`, update `seasons_watched`) and re-import via CSV or JSON sync.
The CLI equivalent is `python3.11 -m src.cli library export` — see [CLI.md](CLI.md#library-management).

## Credential storage

All sensitive credentials (API keys, OAuth tokens) are encrypted at rest using
Fernet symmetric encryption. The encryption key is stored at
`data/.credential_key` by default, or at the path specified by the
`RECOMMENDINATOR_KEY_PATH` environment variable. If you move the database to a
new host, copy the key file too.
