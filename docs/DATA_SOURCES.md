# Data Sources

Every source has its own setup guide, linked below. This page covers what they
share: managing sources, parallel sync, and library export.

## Available sources

| Source | Type | Setup guide | Import method |
|--------|------|-------------|---------------|
| **Goodreads** | Books | [goodreads_csv](../src/ingestion/sources/goodreads_csv/README.md) | CSV export |
| **Goodreads (RSS)** | Books | [goodreads_rss](../src/ingestion/sources/goodreads_rss/README.md) | Public shelves, no CSV export needed |
| **The StoryGraph** | Books | [storygraph_csv](../src/ingestion/sources/storygraph_csv/README.md) | CSV export |
| **Calibre-Web** | Books | [calibre_web](../src/ingestion/sources/calibre_web/README.md) | OPDS |
| **Steam** | Games | [steam](../src/ingestion/sources/steam/README.md) | Steam Web API |
| **GOG** | Games | [gog](../src/ingestion/sources/gog/README.md) | OAuth, library and wishlist |
| **Epic Games** | Games | [epic_games](../src/ingestion/sources/epic_games/README.md) | OAuth via Legendary |
| **Sonarr** | TV Shows | [sonarr](../src/ingestion/sources/sonarr/README.md) | Sonarr API |
| **Radarr** | Movies | [radarr](../src/ingestion/sources/radarr/README.md) | Radarr API |
| **Trakt** | TV Shows / Movies | [trakt](../src/ingestion/sources/trakt/README.md) | OAuth device code: watched history, ratings, watchlist |
| **ROM Library** | Games | [roms](../src/ingestion/sources/roms/README.md) | Scans emulator ROM directories |
| **CSV** | Any | [generic_csv](../src/ingestion/sources/generic_csv/README.md) | Mappable columns |
| **JSON** | Any | [generic_json](../src/ingestion/sources/generic_json/README.md) | JSON or JSONL |
| **Markdown** | Any | [markdown](../src/ingestion/sources/markdown/README.md) | Readable lists |

`templates/` holds an example file per content type and format. Templates carry
`ignored` for excluding items from recommendations, and the TV templates carry
`seasons_watched` (`1,2,5,6` in CSV, `[1,2,5,6]` in JSON).

## Adding, editing and removing sources in the UI

The **Data** tab lists every source as an accordion, enabled ones first. A
disabled source stays listed, muted with a **Disabled** badge and an inert Sync
button, and both `Sync All` and its own Sync button skip it. Each accordion
carries an Enable/Disable toggle.

Two ways to create a source:

- **+ Add source**, at the top of the Sync Sources card. Pick a plugin, give the
  source an id, fill in the plugin's fields. Sensitive fields are password
  inputs, stored encrypted rather than in the plaintext config. Use **Replace**
  in the source's panel to rotate a secret later.
- Define it under `inputs:` in `config.yaml`, then click **Migrate to DB** in
  its panel. After migration the YAML entry is ignored and every edit goes
  through the UI.

Every field in a source's config schema is then editable inline or through
`python3.11 -m src.cli source`. Run `source schema <id>` to see which fields a
given plugin exposes.

**Remove** drops a DB-backed source and clears the credentials its plugin
currently declares sensitive. A secret is left behind when the plugin is no
longer installed or the field is no longer marked sensitive. Remove deletes no
library items, and the app cannot delete one at all, so the rows a removed
source wrote stay put and a re-added source syncs straight back into them.

Sensitive values are never returned by the API. The UI shows a "set" / "unset"
badge with **Replace** and **Clear** actions.

Full CLI reference: [CLI.md](CLI.md#source-management).

## Parallel sync

Each source syncs on its own worker thread, so a run costs the slowest source
rather than the sum of all of them. Set the pool from **Settings** (Sync
section) or the CLI:

```bash
python3.11 -m src.cli settings set sync.max_workers 8   # default 4, 1 for sequential
```

The stored value wins over any `sync.max_workers` left in `config.yaml`. Pass
`--workers N` to override one run: `python3.11 -m src.cli update --workers 8`.
Per-source rate limits are enforced inside each plugin and are untouched by this.

## Library export

Filter the **Library** tab by content type, consumption status and enrichment
state, then pick CSV or JSON and click **Export**. The enrichment filter finds
items still missing metadata so you can fill them in by hand, see
[ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md#manual-enrichment-editing). The CLI
equivalent is `python3.11 -m src.cli library export`, see
[CLI.md](CLI.md#library-management).

Exported files match the import template format, so you can edit one and
re-import it. What a re-import may change depends on the field, and for most
fields it is less than you would expect:

| Field | What a re-import can do to it |
|-------|-------------------------------|
| `rating`, `review` | Fill an empty value. Never replaces one you wrote in the app |
| `status` | Move forward only, unread to consuming to completed. Never reverts a completion |
| `date_completed` | Replace it with a later date only |
| `ignored` | Whatever the file states, in either direction. An absent column, a blank cell or a JSON `null` all say nothing, and your value stands |
| `genre` | Merge in. An import never removes a genre |
| `total_seasons` | Raise it. A smaller number is discarded |
| `seasons_watched` | Fill an empty list. An existing list always wins, so edit seasons from the season checklist or `library edit --seasons-watched` |
| `year`, `year_published`, `pages`, `isbn`, `runtime_minutes`, `platform`, `hours_played`, `notes` | Fill an empty value, and nothing else ever. There is no edit surface for these either, so fix them at the source they came from |
| creator: `author`, `director`, `creator`, `developer` | Fill an empty value, and nothing else. There is no edit surface for it either |

The edit modal and `library edit` cover status, rating, review, seasons watched,
genres, tags and description, and nothing else. `notes` is the fill-only column
most likely to catch you out, being universal and far more inviting to hand-edit
than an ISBN.

One thing does move a status backward, and it is not a status rule. A completed
TV show whose season checklist you have filled in returns to in-progress when an
import raises its `total_seasons` above the seasons you have watched.

> **An export is a snapshot, not a patch.** Every row this app exports carries a
> real `true` or `false` in `ignored`, never a blank cell, so re-importing an
> export replaces your entire ignore list with the state it had at export time.
> Everything you have ignored since is un-ignored. That is how you un-ignore
> items in bulk, and it is why you **must not leave a one-off export configured
> as a standing source**. Left configured, it re-asserts that stale snapshot on
> every sync. Import it, then remove or disable the source.

`year`, `runtime_minutes`, `total_seasons`, `platform`, `hours_played` and the
creator columns (`director`, `creator`, `developer`) used to export blank and now
export correctly, so retake any export you keep as a backup. Two things still do
not round trip:

- **`platform` on a GOG library synced before the platform-shape fix.** It is
  fill-only, so the stale value beats every later sync and exports as a Python
  repr (`{'windows': True, ...}`), which a re-import would store straight back
  as that literal string. Nothing in the app clears it, and removing the GOG
  source and re-adding it does not either, because the items stay and fill-only
  keeps the value.
- **`hours_played` on a video game a generic CSV or JSON import brought in
  before that column was renamed** to the library's own `playtime_hours`. Those
  rows still carry the old key and export blank. The number is not lost, and
  syncing that source again writes the new key. Nothing but the export is
  affected, because the
  [length scorer](SCORING.md#content-length-preferences) has only ever read
  `playtime_hours`.

## Credential storage

API keys and OAuth tokens are encrypted at rest with Fernet. The key lives at
`data/.credential_key`, or wherever `RECOMMENDINATOR_KEY_PATH` points. Copy it
alongside the database if you move to a new host. See
[SECURITY.md](SECURITY.md#credential-encryption).
