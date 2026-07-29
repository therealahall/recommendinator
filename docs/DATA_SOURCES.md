# Data Sources

Recommendinator imports your library from multiple sources through a plugin
architecture. Each source has its own setup guide; the table below links to it.
This document covers the parts that are shared across every source: managing
sources in the UI/CLI, parallel sync, and library export.

## Available sources

| Source | Type | Setup guide |
|--------|------|-------------|
| **Goodreads** (file import) | Books | [goodreads_csv](../src/ingestion/sources/goodreads_csv/README.md) — CSV export from your Goodreads library |
| **Goodreads (RSS)** | Books | [goodreads_rss](../src/ingestion/sources/goodreads_rss/README.md) — sync public shelves via RSS (user ID or profile URL; no CSV export) |
| **The StoryGraph** (file import) | Books | [storygraph_csv](../src/ingestion/sources/storygraph_csv/README.md) — CSV export from your StoryGraph library |
| **Calibre-Web** | Books | [calibre_web](../src/ingestion/sources/calibre_web/README.md) — OPDS import from a Calibre-Web instance |
| **Steam** | Games | [steam](../src/ingestion/sources/steam/README.md) — automatic import via Steam Web API |
| **GOG** | Games | [gog](../src/ingestion/sources/gog/README.md) — OAuth; imports library and wishlist |
| **Epic Games** | Games | [epic_games](../src/ingestion/sources/epic_games/README.md) — OAuth via Legendary |
| **Sonarr** | TV Shows | [sonarr](../src/ingestion/sources/sonarr/README.md) — import from Sonarr API |
| **Radarr** | Movies | [radarr](../src/ingestion/sources/radarr/README.md) — import from Radarr API |
| **Trakt** | TV Shows / Movies | [trakt](../src/ingestion/sources/trakt/README.md) — OAuth device-code; imports watched history, ratings, and watchlist |
| **ROM Library** | Games | [roms](../src/ingestion/sources/roms/README.md) — scan emulator ROM directories |
| **CSV** (file import) | Any | [generic_csv](../src/ingestion/sources/generic_csv/README.md) — generic CSV with customizable mapping |
| **JSON** (file import) | Any | [generic_json](../src/ingestion/sources/generic_json/README.md) — generic JSON/JSONL import |
| **Markdown** (file import) | Any | [markdown](../src/ingestion/sources/markdown/README.md) — human-readable markdown lists |

Import file examples live in the `templates/` directory. Templates support the
`ignored` field for excluding items from recommendations, and TV show templates
use a `seasons_watched` list (e.g., `1,2,5,6` in CSV or `[1,2,5,6]` in JSON) to
track specific seasons watched.

**The Goodreads and StoryGraph CSV exports and the generic CSV, JSON, and
Markdown formats are one-shot file imports, not sources.** You hand the file to
the app once, through the web **Data** tab's **Import from file** button or the
CLI `import` command, and it runs through the ingestion pipeline immediately.
Nothing is stored about the file and nothing re-syncs, so to refresh, export
again and import again. Every other source in the table (including Goodreads RSS
and the ROM Library) is configured once and synced repeatedly. See
[Importing from a file](#importing-from-a-file) below.

## Importing from a file

> **Breaking change:** `goodreads_csv`, `storygraph_csv`, `csv_import`,
> `json_import`, and `markdown_import` are no longer creatable or syncable
> sources. They do not show up in **+ Add source** or `source plugins`, and
> `source create` rejects them with a pointer to the import flow. If you already
> have a source row or a legacy `config.yaml` `inputs:` block naming one of them,
> it logs a non-fatal warning at sync time and is skipped. It is left in place
> rather than deleted, and it is still listed — in the **Data** tab and in
> `source list` / `GET /api/sync/sources`, flagged `is_file_import` — with a
> "Not syncable" badge and no Sync button, so you can find it and clear it. A
> database-backed row goes away with the panel's **Remove** button,
> `python3.11 -m src.cli source remove <id>`, or `DELETE /api/sync/sources/<id>`.
> A row that only exists in `config.yaml` has no database row to remove: delete
> its block under `inputs:` instead. Every other per-source operation (`show`,
> `schema`, `set`, `enable`, `migrate`, secrets) reports it as unknown — there is
> nothing to configure on a one-shot import. Goodreads RSS and the ROM Library
> are unaffected: the first polls a feed and the second scans a directory, so
> both remain ordinary syncable sources.

**Web UI:** open the **Data** tab and click **Import from file**. Pick the
source, choose the file, and fill in whatever options that source declares. The
file picker filters to the extensions the chosen importer declares (`.csv`,
`.json`/`.jsonl`, or `.md`/`.markdown`) and names them below the field. The
three generic formats need a `content_type` (book, movie, tv_show, or
video_game). The Goodreads and StoryGraph exports take no options because they
are always books. Submit to import. The import runs as a job, so progress shows
up the same way a sync does, and the result banner reports the counts plus any
per-row errors. Uploads are capped at 50 MB: the modal refuses a larger file as
soon as you pick it, the server refuses a request body over that cap (plus a
small allowance for the multipart framing) before reading it, and the endpoint
checks the file itself again while writing it out. Files saved with a UTF-8
byte-order mark (what Excel writes) are read correctly by every importer, but the
file does have to be UTF-8 text: a Latin-1 or UTF-16 export is refused with a
message telling you to re-save it as UTF-8.

**CLI:** use the `import` command, documented in full at
[CLI.md](CLI.md#importing-from-a-file). The CLI has no size cap, because it reads
a local file you already trust.

```bash
python3.11 -m src.cli import --source goodreads_csv --file inputs/goodreads_library_export.csv
python3.11 -m src.cli import --source storygraph_csv --file inputs/storygraph_export.csv
python3.11 -m src.cli import --source csv_import --file my_movies.csv --content-type movie
python3.11 -m src.cli import --source list   # show importable sources and their options
```

Options come from the source's own schema, so `--content-type` and `--option
KEY=VALUE` only accept keys that source declares — `import --source list` shows
them, along with the file extensions each importer reads. Anything else is
refused before the import runs rather than silently ignored, and the same gate
runs for the upload endpoint: a multipart field the plugin does not declare is a
400 naming the key, not a value quietly dropped.

Both interfaces report the same result: `message`, `source`, `items_synced`,
`total_items`, `errors`, and `warning`. A file that parses but contains no items
is a success carrying a `warning` (the web banner switches to its warning style
with a ⚠ marker, the CLI prints a `Warning:` line) rather than an error, so an
import that quietly does nothing still explains itself. Rows that fail
individually come back in `errors` instead, and `warning` stays `null`.

## Adding, editing, and removing sources in the UI

The **Data** tab renders every configured source as an accordion. Both enabled
and disabled sources are shown — disabled accordions appear muted with a
"Disabled" badge and a non-actionable Sync button. Leftover file-import entries
are shown last, muted, badged "Not syncable" and with no Sync button at all;
their panel explains what they are and offers Remove. Everything else is sorted
enabled-first.

There are two ways to create a source:

- Click **+ Add source** at the top of the Sync Sources card. Pick a plugin from
  the dropdown, give the source an id, and fill in the plugin's fields — including
  sensitive fields (passwords, API keys, OAuth tokens), which render as password
  inputs and are stored encrypted, never written to the plaintext config. Click
  Create once and the source goes straight into the database, ready to use — no
  YAML edit and no follow-up secret step required. The Replace action in the
  source's expanded panel remains available to rotate a secret later.
- Define the source under `inputs:` in `config.yaml`, then click **Migrate to DB**
  in the source's expanded panel to copy the YAML entry into the database. After
  migration the YAML entry is ignored — all edits go through the UI.

This applies to syncable sources only. The five file-import plugins
(`goodreads_csv`, `storygraph_csv`, `csv_import`, `json_import`,
`markdown_import`) are not offered in the plugin picker at all, and creating one
is rejected. See [Importing from a file](#importing-from-a-file).

Once a source is in the database, every field defined in its plugin's config
schema is editable inline from the web UI or via the
`python3.11 -m src.cli source` CLI commands. The exact set of fields differs per
plugin (e.g. Steam exposes `api_key` and `vanity_url`; Sonarr exposes `url` and
`api_key`). Run `python3.11 -m src.cli source schema <id>` to see what is editable
for a given source.

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
different APIs. Set the worker pool from the **Settings** page (Sync section), or
the CLI:

```bash
python3.11 -m src.cli settings set sync.max_workers 8   # default 4; 1 for sequential
```

The value is stored in the database and wins over any `sync.max_workers` left in
`config.yaml`. The CLI accepts `--workers N` to override per-invocation, e.g.
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
items as `ignored`, update `seasons_watched`) and re-import via the CSV or JSON
file import (web **Import from file** button or `import --file`). The CLI
equivalent of export is `python3.11 -m src.cli library export` — see
[CLI.md](CLI.md#library-management).

## Credential storage

All sensitive credentials (API keys, OAuth tokens) are encrypted at rest using
Fernet symmetric encryption. The encryption key is stored at
`data/.credential_key` by default, or at the path specified by the
`RECOMMENDINATOR_KEY_PATH` environment variable. If you move the database to a
new host, copy the key file too.
