# Data Sources

Recommendinator imports your library from multiple sources through a plugin
architecture. Each source has its own setup guide; the table below links to it.
This document covers the parts that are shared across every source: managing
sources in the UI/CLI, parallel sync, and library export.

## Available sources

| Source | Type | Setup guide |
|--------|------|-------------|
| **Goodreads** | Books | [goodreads_csv](../src/ingestion/sources/goodreads_csv/README.md) — CSV export from your Goodreads library |
| **Goodreads (RSS)** | Books | [goodreads_rss](../src/ingestion/sources/goodreads_rss/README.md) — sync public shelves via RSS (user ID or profile URL; no CSV export) |
| **The StoryGraph** | Books | [storygraph_csv](../src/ingestion/sources/storygraph_csv/README.md) — CSV export from your StoryGraph library |
| **Calibre-Web** | Books | [calibre_web](../src/ingestion/sources/calibre_web/README.md) — OPDS import from a Calibre-Web instance |
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
  the dropdown, give the source an id, and fill in the plugin's fields — including
  sensitive fields (passwords, API keys, OAuth tokens), which render as password
  inputs and are stored encrypted, never written to the plaintext config. Click
  Create once and the source goes straight into the database, ready to use — no
  YAML edit and no follow-up secret step required. The Replace action in the
  source's expanded panel remains available to rotate a secret later.
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

Exported files match the import template format, so you can edit them (e.g. flip
`ignored` on a batch of items, or rate something the import left unrated) and
re-import via CSV or JSON sync. What a re-import is allowed to change depends
on the field, and for most fields the answer is "less than you would expect":

- **Rating and review** are filled only while the stored value is empty. A
  re-import never replaces one you wrote in the app.
- **Status** only moves forward (unread → consuming → completed). A file can
  advance a status you moved backward, but it can never revert a completion —
  except for one case that is not a status rule at all: a completed TV show
  whose season checklist you have filled in returns to in-progress when the
  import raises its `total_seasons` above the seasons you have watched.
- **Completion date** is replaced only by a later one.
- **`ignored`** does whatever the file actually states. A real `true` or `false`
  wins in either direction, so the export-edit-re-import round trip is how you
  un-ignore items in bulk. Leaving the column out, leaving a cell blank, or
  sending a JSON `null` all mean the file says *nothing* about the flag, and the
  value you set in the app stands. **That protects a file you maintain by hand,
  not an export from this app** — see the warning below.
- **`genre`** is additive. An imported genre is merged into the genres already
  stored; it never replaces them, and there is no way to remove one through an
  import.
- **`total_seasons`** is monotonic — it only ever increases. A smaller number in
  the file is discarded.
- **`year`, `year_published`, `pages`, `isbn`, `runtime_minutes`, `platform`,
  `hours_played` and `notes`** are fill-only: they are written only while the
  library has no value, so editing one in an export and re-importing does
  nothing. Whichever source or enrichment provider filled the field first owns
  it, and there is no edit surface for these — the edit modal and `library edit`
  cover status, rating, review, seasons watched, genres, tags and description,
  and nothing else. Fix the value at the source it came from. **`notes` is the
  one most likely to catch you out**: it is a universal column on every
  template, not a type-specific one, and it is far more inviting to hand-edit
  than an ISBN. It is fill-only for a different reason than the detail-table
  columns in that list — it is not one of them but a key in the free-form
  metadata blob, which merges with existing keys winning. `hours_played` is the
  other blob key, and the effect is identical either way.
- **`seasons_watched`** is fill-only too, and it is the one most worth knowing
  about: an existing list always wins, so editing the season numbers in an
  export and re-importing changes nothing. Change them from the edit modal's
  season checklist or `library edit --seasons-watched`.
- **The creator column** — `author` for books, `director` / `creator` /
  `developer` for the other three types — round trips for books only. Books
  store the author in their own detail table, so it exports and re-imports
  intact. For movies, TV shows and video games the library has nowhere to put
  it: the import reads the column and then drops it, so editing it in an export
  does nothing, and it exports blank besides. The director, creators and
  developer the app *does* show come from a plugin or an enrichment provider,
  and no template column reaches them.

> **An export is a snapshot, not a patch.** Every row this app exports carries a
> real `true` or `false` in `ignored`, never a blank cell, so re-importing an
> export replaces your entire ignore list with the state it had at export time —
> every item you have ignored since is un-ignored. **Do not leave a one-off
> export configured as a standing source.** If you do, every later sync
> re-asserts that stale file, so your ignores are wiped again and again rather
> than once. Import it, then remove or disable the source.

Exports also changed in this release: `year`, `runtime_minutes`, `total_seasons`,
`platform` and `hours_played` previously came out **blank**, because the export
looked them up under the template's column name rather than the name the library
stores them under (`year` is stored as `release_year`, `platform` as `platforms`,
and so on). The first four were blank for *every* item, whatever it was imported
from, since the library consumes those spellings into their columns and nothing
is left under the template name; only `hours_played` still exported for items a
generic CSV or JSON import had brought in. The first four now export for every
item. `hours_played` moved as well, to the name the library uses for it
(`playtime_hours`). Any game already storing its playtime under that name
therefore *gains* the column, every Steam game in the library included — the
Steam plugin has always written that spelling, which is exactly why the old
export came out blank for it. What no longer exports is a game a generic CSV or
JSON import brought in before this release, which is the third exception below.

If you keep an export as a backup, retake it — but it is still not a complete
one, and there are three exceptions rather than one.

The first is the creator. `director`, `creator` and `developer` remain blank on
every movie, TV show and video game, for the reason the creator bullet above
gives. Books are unaffected.

The second is **`platform` on a GOG library synced before this release**. The
GOG plugin used to write the platform list in the wrong shape, and that fix is
forward-only: `platform` is fill-only, so the stored value wins over every later
sync and no amount of re-syncing will replace it. The export fix above is what
makes this visible — the column used to come out blank, and now it emits the
stored value as a Python repr (`{'windows': True, ...}`), which a re-import
would store right back as that literal string. **There is nothing you can do
about it from inside the app.** Removing the GOG source and adding it again
does not help: Remove drops that source's config and the secrets its plugin
currently declares sensitive — a secret is left behind when the plugin is no
longer installed, or when the field holding it is no longer marked sensitive —
and leaves every item it wrote exactly where it is, so a re-add syncs into those
same rows — matched by external id, or by normalised title — and the fill-only
rule keeps the stale value. Clearing the affected items is not an option
either, because the app has no way to delete a library item, from the web UI or
the CLI. The stale value survives every later sync.

The third is **`hours_played` on a video game a generic CSV or JSON import
brought in before this release**.
Both the import and the export used to spell that column `hours_played` in the
free-form metadata, and both now use the library's own name for it,
`playtime_hours`. Nothing rewrites the key already stored on existing rows, so a
game a generic CSV or JSON import brought in earlier still carries the old
spelling and exports blank. Nothing is lost: the number is still in the
database under `hours_played`, and syncing that source again writes the new key
alongside it, after which the column exports normally. Nor does the gap reach
anything but the export — the [length scorer](SCORING.md#content-length-preferences)
reads `playtime_hours` and never read the old spelling, so those games did not
feed it before this release and do not now.

So a retaken backup restores everything except the creator on three of the four
content types, `platform` on games from a GOG library that predates the fix, and
`hours_played` on games a generic CSV or JSON import brought in before it.

The CLI equivalent is `python3.11 -m src.cli library export` — see [CLI.md](CLI.md#library-management).

## Credential storage

All sensitive credentials (API keys, OAuth tokens) are encrypted at rest using
Fernet symmetric encryption. The encryption key is stored at
`data/.credential_key` by default, or at the path specified by the
`RECOMMENDINATOR_KEY_PATH` environment variable. If you move the database to a
new host, copy the key file too.
