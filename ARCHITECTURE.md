# Architecture Documentation

## System Components

### 1. Data Ingestion (`src/ingestion/`)

Parses and normalizes data from external sources, plus one-off files through
`src/ingestion/importers/`.

| Sources | Content |
|---------|---------|
| Goodreads RSS shelves, Calibre-Web OPDS | Books |
| Steam Web API, GOG OAuth, Epic via Legendary, ROM library scanner | Video games |
| Sonarr | TV shows |
| Radarr | Movies |
| Trakt device-code OAuth | TV shows and movies |

- Plugins subclass `SourcePlugin` (`plugin_base.py`), auto-discovered from
  `src/ingestion/sources/` by `PluginRegistry`. Each validates its own config,
  fetches, and normalizes ratings.
- Sources are **named instances**: a user-defined id plus a `plugin:` naming the
  type, so one plugin can back several. `ContentItem.source` carries the id, not
  the plugin name. `resolve_inputs()` (`src/sources/service.py`) resolves
  entries to `(source_id, plugin, config)`.
- `execute_multi_source_sync` is shared by CLI and web. Each enabled source runs
  on its own thread (`sync.max_workers`, default 4) and results keep input
  order. Rate limits are per source, so cross-source parallelism is safe.
- The web `SyncManager` aggregates progress callbacks into a `sources` map, and
  files each error under the source that produced it, so the Data tab shows a
  plugin's own wording on that source's row.
- Source config is writable over HTTP, so two field kinds are constrained rather
  than trusted. `paths.py` contains a scanner's path inside
  `security.allowed_source_roots`, a config.yaml key the settings API cannot
  reach; `urls.py` checks a base URL's shape. A `credential_bound` `ConfigField`
  (a plugin's `url`) names the host its secrets are sent to.

### 2. Storage (`src/storage/`)

SQLite holds everything.

| Table | Holds |
|-------|-------|
| `users` | Per-user settings (JSON) |
| `content_items` | Library items, scoped by `user_id` |
| `content_item_external_ids` | The id each source knows an item by, unique per `(user_id, source, external_id, content_type)` |
| `book_details`, `movie_details`, `tv_show_details`, `video_game_details` | Per-type detail |
| `credentials` | Encrypted OAuth tokens and API keys, per-source and global |
| `source_configs` | Non-sensitive per-source config |
| `settings` | Global config, dotted leaf key to JSON value, only what a user set |
| `enrichment_status` | Enrichment tracking |
| `enrichment_job` | The live enrichment run, one row, so either interface can watch and stop it |
| `sync_runs` | One row per sync run: outcome, item counts and errors, pruned per source. An unfinished row claims its source, so no two processes sync it at once |
| `preference_profiles` | The generated per-user taste profile |

#### User-owned fields

`rating`, `review`, `status`, `date_completed` and `ignored` belong to the user.

`save_content_item` is the sync door:

| Field | Rule |
|-------|------|
| `rating`, `review` | Fill-only, written only into an empty column, so a re-import cannot erase either |
| `status` | Forward-only, through `resolve_status_forward` (`src/storage/merge.py`) |
| `date_completed` | Later date wins |
| `ignored` | Only a stated `True` or `False` wins, in either direction. `None` leaves the stored flag alone |

`seasons_watched` is the one metadata key the sync door unions: a sync adds a
season, never removes one.

One exception to forward-only sits outside that resolution. After the upsert,
`_handle_tv_season_change` regresses a completed TV show to
`currently_consuming` when the season count rises above the seasons the
user checked off, because new seasons mean the show is not finished. It needs an
existing `seasons_watched` list, and it skips ignored items.

The enrichment door runs that same pass:

- **`save_enrichment_metadata`** writes a provider's metadata to the detail
  table and the derived columns. Of the user-owned fields it writes only
  `status`, and only through the season regression above.

The three user-action doors overwrite freely and write only what the caller
supplied:

- **`complete_content_item`** backs the `complete` CLI command and
  `POST /api/complete`. It finds or creates the row and applies rating, review,
  status and date in one transaction. `status` is written outright rather than
  resolved forward.
- **`update_item_from_ui`** backs the web edit modal and `library edit`.
  "Not supplied" is spelled two ways. `status`, `rating` and `review` use the
  `UNSET` sentinel, because for the last two `None` has to mean clear.
  `seasons_watched`, `genres`, `tags` and `description` use `None`, so there the
  *empty* value clears: `[]` or `""`.
- **`set_item_ignored`** backs the Ignore buttons
  (`PATCH /api/items/{id}/ignore`) and `library ignore` / `library unignore`. It
  writes `ignored` alone.

**`date_completed` is never replaced silently.** A completion carrying no date
fills an empty column with today and keeps an existing date. A named date is
written as given, but no further ahead than `MAX_COMPLETION_DATE_SKEW` — one
day, for a caller in a zone ahead of the server — and the check is at the door,
so no surface can skip it.

#### Source configuration precedence

A source lives in YAML only (bootstrap), YAML plus DB (migrated, DB authoritative
and the YAML entry ignored), or DB only (`+ Add source` or `source create`, never
touching `config.yaml`).

- Listing endpoints return every known source with its `enabled` flag.
- `resolve_inputs` gates sync execution, filtering disabled and unknown-plugin
  entries before any plugin runs, and merging encrypted credentials over the rest
  of the config.
- Sensitive fields (`ConfigField(sensitive=True)`) always live in `credentials`,
  whichever side owns the rest.
- `POST /api/sync/sources/<id>/migrate` splits a YAML entry across both tables
  and is idempotent. `POST /api/sync/sources` writes only non-sensitive values,
  and secrets follow through `PUT /api/sync/sources/<id>/secret/<key>`.
- Deleting the last source on a plugin also deletes any credential left under
  that plugin's own name by an older release; a sibling still on the plugin
  keeps it.

#### Global configuration precedence

**const default < YAML < database**, for `recommendations`, `sync`,
`enrichment`, `web` and `logging`.

1. Const defaults for every in-scope leaf, declared in `src/settings/metadata.py`
   (`default_config()`).
2. `config.yaml`, deep-merged over them.
3. The `settings` table, keyed by dotted leaf path, holding only what a user set.

`migrate_config_settings` assembles this on every boot and hot-reload, replacing
`config[section]` in place. **Nothing is written to the database here**, so a
fresh install runs on an empty `settings` table.

`storage` is out of scope and stays YAML-only, because it bootstraps the database
itself. `inputs` and credentials belong to the `source_configs` and `credentials`
migrations.

**Secrets are never plaintext.** `migrate_config_secrets`
(`src/storage/global_secrets.py`) sweeps every `sensitive` registry leaf out of
the in-memory config into `credentials`, under a reserved `settings:`
`source_id`, and strips it from the running config. Enrichment reads them back at
runtime. The Settings page and `settings` CLI expose them write-only.

**One-time migrations.** `create_schema` (`src/storage/schema.py`) runs on every
database open. The settings and content steps are guarded by `PRAGMA
user_version`: it reads the stored version once, runs each step the database is
below, and stamps `_SCHEMA_VERSION` at the end, inside the same transaction. A
database with no tables yet reports as already current.

Versions 1, 2 and 6 prune the `settings` rows the app can no longer reach.

Version 3 is `_repair_legacy_content_rows`: an approximate SQL
`normalized_title` backfill and the stranded detail-shape repair.

No step merges or unmerges rows: an upgraded library rewrites its keys and
leaves the merge door to decide the rest.

`src/storage/schema.py` says which versions guard a step and which only record
a shape.

The one table rebuild, `_move_external_ids_off_content_items`, guards on the
`content_items.external_id` column rather than the version, since it commits
before the stamp. `_assert_no_child_followed` aborts it when SQLite ignores the
`legacy_alter_table` pragma, rather than commit a database no write can use.

#### Derived sort and search columns

`content_items.sort_title` and `content_items.search_text`
(`src/storage/derived.py`) hold `get_sort_title(title)` and the item's
normalized title, creator and series, so `get_content_items` orders in SQL
under a real `LIMIT`/`OFFSET` and builds a `ContentItem` only for the rows it
returns. Every `ORDER BY` ends in `ci.id`, because SQL ordering is not stable
and a page boundary inside a tie repeats one row and drops another.

A search is one matched set. SQL orders the filtered candidates and projects
each as an `id` and its `search_text`; `search_text_matches` runs all three
tiers — exact, substring and the fuzzy window scan SQL cannot express — and the
page is sliced out of what matched, so no candidate that misses and no match
outside the page costs a `ContentItem`.

#### Cross-source deduplication

A save looks up the item its source knows by `(source, external_id)`, falling
back to the normalized title, oldest row first. It skips a row whose merge
group holds another id from that source: a source lists an item once, so two
of its ids are two items.

The key is deliberately lossy — a region qualifier, an edition and a trailing
year all leave it — and three vetoes make it safe. A creator, a
year or a region both rows state and disagree on refuses the match, as does a
year one row spells into its title against a row stating none.

External ids live in `content_item_external_ids`, one per source per item:
Steam's app 440 and GOG's product 440 are different games.

`content_items.source` is display provenance and no sync overwrites it.

The rules every merge follows:

- `rating` and `review` fill from the duplicate only into a null
- `date_completed` keeps the later date
- `status` takes the further-advanced under the sync ordering
- `ignored` moves nowhere: each row keeps its own
- Genres and tags merge additively, monotonic columns (seasons, episodes) keep
  the higher value, and detail metadata merges existing-wins
- `seasons_watched` is the exception, unioned across both rows, and
  `seasons_watched_dates` merged per season keeping the later watch date, so an
  ingestion date never overrides a user date

Nothing deletes a row to dedup (`src/storage/item_merges.py`). The absorbed row
keeps every column, sets `merged_into` and drops out of every read, and every
write door refuses it; `content_item_merges` records survivor, absorbed,
evidence (so far only the operator's own choice) and what it overwrote.

`unmerge_content_items` writes that back: a record holds the columns that merge
itself moved, so an undo puts those back and leaves a rating, a description or
an enrichment run that landed since where it stands.

#### Detail-shape repairs

`_migrate_stranded_detail_shapes`, in the same `create_schema` pass, rewrites
shapes storage no longer writes and no re-sync corrects. A `total_seasons`
duplicated in a TV show's metadata blob moves onto the `seasons` column, taking
the higher of the two so the count is never lowered. It runs inside the one
transaction `create_schema` commits, so a failure after it discards it. Rows already
in the current shape are untouched, so re-running is a no-op.

#### Thread safety

WAL mode, and `_get_connection` sets `PRAGMA busy_timeout = 5000` so concurrent
writers block instead of raising `SQLITE_BUSY`. A per-`StorageManager`
`threading.Lock` serialises the read-resolve-write dedup merge against the
parallel sync executor. `StorageManager.save_content_item`,
`complete_content_item`, `save_enrichment_metadata`, `credentials.save` and
`merge_user_preference_config` all take it — the last because
`PUT /api/users/{id}/preferences` is a partial merge, and two of them at once
would otherwise each write a `users.settings` blob read before the other
landed, losing one wholesale.

### 3. Recommendations (`src/recommendations/`)

A unified scoring pipeline that always runs, across content types.

```
RecommendationEngine
  |-- ScoringPipeline (always runs; its aggregate IS the emitted score)
  |     |-- GenreMatchScorer        genre preference, dislike included
  |     |-- CreatorMatchScorer      author/director/creators/developer
  |     |-- TagOverlapScorer        threshold and cluster tag overlap
  |     |-- SeriesOrderScorer       next in sequence
  |     |-- RatingPatternScorer     rating history in matching genres
  |     |-- ContentLengthScorer     soft penalty for length mismatch
  |     |-- ContinuationScorer      actively consumed items (dropped when none exist)
  |     |-- SeriesAffinityScorer    well-rated franchises (avg >= 4)
  |     |-- AdaptationScorer        cross-media adaptations (dropped when none exist)
  |     |-- [CustomPreferenceScorer]    when the user has natural language rules
  |
  |-- UserPreferenceConfig (per-user weight overrides)
  |-- Variety penalty (variety.py) when variety_penalty > 0
```

Every contribution to the score is one of those scorers, so the Score Details
panel's rows and their weights reproduce the number displayed beside them.
See [docs/SCORING.md](docs/SCORING.md).

**Weights resolve const default < `config.yaml` < `settings` table < per-user.**
`min_rating_for_preference` and the counts have no per-user field.

Invariants:

- The engine's output is one declared record (`record.py`). Both interfaces
  serialise it through `to_payload`, which is what keeps `recommend --format
  json` and `GET /api/recommendations` one document.
- The taste signal is completed items that are **rated** and **not ignored**,
  across all content types. Nothing else shapes preferences or scoring. Two
  consumption facts sit outside it and are answered from wider sets: series
  ordering, from the full completed set, and the variety penalty below, which
  multiplies the final score rather than contributing to it.
- Ignored items are filtered from the candidate pool at fetch time, as they are
  from the signal set.
- Series filtering with substitution (`series_in_order`) replaces a candidate
  failing the ordering rules with the earliest recommendable entry in its series,
  scored on its own merits, once per series.
- The variety penalty multiplies a candidate's final score by `1 - penalty`,
  taking the strongest penalty among its recently finished genre clusters. See
  [SCORING.md](docs/SCORING.md#variety-after-completion).

**Cross-content-type matching is a lookup, not a model.** Genre clusters
(`genre_clusters.py`) map raw genre and tag terms onto a fixed set of thematic
clusters, so a book tagged "space warfare" reaches a TV show tagged "war".
Compound terms like "Sci-Fi & Fantasy" split first (`genre_normalizer.py`).

**Adaptations and reference items are matched through an index**
(`reference_index.py`), built once per request over the taste signal. The
adaptation lookup runs once per candidate, before scoring. The reference lookup
runs after the slice, once per emitted recommendation, because nothing but those
records reads its result.

### 4. Enrichment (`src/enrichment/`)

Background metadata gap-filling from external APIs. Providers subclass
`EnrichmentProvider` and are discovered by name from `src/enrichment/providers/`
and from `private/plugins/`, each with its own token-bucket rate limiter. A
background worker runs them in configurable batches, and an optional hook fires
it after a sync.

| Provider | Content | Franchise source |
|----------|---------|------------------|
| TMDB | Movies, TV | `belongs_to_collection`, position by release date |
| OpenLibrary | Books, no API key | none |
| RAWG | Video games | `GET /games/{id}/game-series`, position by release date |

RAWG derives a franchise name from the longest common prefix of the related
titles, after majority first-word voting drops outliers, and strips DLC suffixes
before searching. Both providers store `franchise` and `series_position` in
`extra_metadata` for series ordering.

Rules:

- The merge is gap-filling and never overwrites existing metadata. Manual edits
  are the exception: genres, tags and a description set from the edit modal or
  `library edit` overwrite the detail table, record the `"manual"` provider, and
  leave the automatic queue for good.
- **A settled miss is not a failure.** Every provider answering "not this one"
  retires the item through `mark_enrichment_complete(..., "not_found")`. Reaching
  it again takes `--retry-not-found`.
- **A failure is classified before it is acted on** (`_classify_failure`,
  `_is_retryable`). Transport errors, 5xx, 408 and 429 are retryable, so
  `mark_enrichment_failed` records the error and leaves `needs_enrichment=1`. Any
  other 4xx would be rejected identically every run and is not retryable.
- **A provider that keeps rejecting is abandoned for the run.** Five consecutive
  non-retryable rejections (`_MAX_CONSECUTIVE_REJECTIONS`) drop it, and the run
  ends once nothing unabandoned is left for its content type.
- **A failed save is ours, not a miss.** `mark_enrichment_settled_failure` takes
  the item out of the queue with the error on the row, so it is not counted as
  one more `not_found`.
- An item counts as enriched only with a real provider, no error, not
  `not_found`, and `needs_enrichment=0`. `get_content_items(enrichment=...)` and
  the per-row `enriched` flag share that predicate (`_ENRICHED_PREDICATE`).
- A run skips items it already attempted, so a queued failure is tried once per
  run.

### 5. Interfaces

The CLI and the web UI are **alternative interfaces to the same capabilities**,
neither a subset of the other. Every service both call sits outside both
packages: recommendation, ingestion, storage, settings,
`src/config/service.py` (YAML loading, bootstrap resolution, component
factories), `src/sources/service.py` (source config CRUD), `src/auth/` (GOG,
Epic and Trakt OAuth) and `src/utils/export.py`.

Neither interface package imports the other, and each
framework stays in the package it serves: `fastapi` and `starlette` only under
`src/web/`, `click` only under `src/cli/`.

#### CLI surface

**CLI** (`src/cli/`): Click groups `status`, `recommend`, `update`, `complete`,
`source`, `settings`, `preferences`, `enrichment`, `library`, `auth`, `account`,
`profile`, `theme`, most carrying a `--format json` view. Full reference in
[docs/CLI.md](docs/CLI.md).

#### Web surface

**Web** (`src/web/` + `resources/`): a FastAPI REST backend and a Vue 3 SPA,
built by Vite from `resources/js/` and `resources/css/` into
`src/web/static/dist/` with content-hashed filenames. Tabs are Recommendations,
Library, Duplicates, Data, Preferences and Settings. Internal network only.

- The **Settings** page is the UI peer of the `settings` CLI group, over the
  shared `src/settings/service.py`.
- The **Preferences** page carries the generated taste profile, the UI peer of
  `profile show` and `profile regenerate`.
- **Library search is bounded at 200 characters in four places**
  (`MAX_SEARCH_LENGTH` in `src/utils/sorting.py`, mirrored in
  `resources/js/constants/library.ts`). `GET /api/items` answers 422,
  `library list --search` errors, the input caps typing and announces the cap
  to screen readers, and the library store truncates in `setFilter`.
- Library export: `GET /api/items/export?type=book&format=csv`.
- The duplicates review reaches the merge door from both sides: `/api/duplicates`
  and `/api/merges` behind the **Duplicates** page, `library duplicates`,
  `merge`, `unmerge`, `merges`, `decline-duplicate`, `declined-duplicates` and
  `undecline-duplicate` on the CLI. Both serialize through
  `src/utils/duplicate_serialization.py`.
- `sync_scheduler` (`src/web/scheduler.py`) runs on the app's lifespan, ticking
  once a minute and starting one due source, so a backlog staggers instead of
  opening a thread per source. No server, no scheduled sync. It and
  `POST /api/update` build the job through the same `build_sync_job`
  (`src/web/sync_dispatch.py`), so a scheduled run records and enriches as a
  requested one does. The run over every source overlaps nothing: the tick skips
  while it runs, and `POST /api/update` answers **409** either way.

#### Request handling

Every `/api` handler is plain `def`, so Starlette runs all of them in a
threadpool: they do blocking SQLite, scoring and outbound OAuth work with
nothing to await, and on the event loop one of them stalled every other request.
That threadpool is anyio's, capped at **40 tokens**, so enough concurrent slow
requests is where the API stops answering.

- A component a handler **requires** is a parameter of it, annotated with one
  of the `Required*` aliases in `src/web/guards.py` (`RequiredStorage`,
  `RequiredConfig`, `RequiredEngine`). Declaring it in the signature rather than
  calling a guard in the body means it cannot be forgotten while the handler
  still compiles, FastAPI caches it so a handler and its own dependencies
  acquire it once, and it resolves **before** request validation — an invalid
  request to an endpoint whose component is down answers 503, not 422.
- Writing the running config is serialised by one lock in `src/web/state.py`,
  not by the event loop. Four paths write it — `PUT /api/settings`,
  `DELETE /api/settings/{key}`, `POST /api/config/reload` and the config
  watcher.
- The lazily-built process singleton behind `/api/sync/*` (`get_sync_manager`)
  builds under a module-level lock for the same reason: two threadpool requests
  on a cold process would otherwise each get a manager of their own, and a job
  started through one is invisible to the status endpoint reading the other.
- A handler that does **not** require a component reads it through the
  unguarded `get_*` accessors and falls back when it is `None`, still answering
  200: recommendations serve on the engine alone, `POST /api/complete` falls
  back to the registered feature-flag defaults, and `GET /api/status` guards
  none of the four it reads. In `tests/test_web_api.py`,
  `TestUnguardedReadsAreOptional` pins the first two and
  `TestDependencyGuards.test_status_reports_initializing_when_components_are_down`
  pins the third, so a new handler picks one or the other: guard the read, or
  make the fallback real.

## Data Flow

```
Data Sources (APIs, CSV, JSON, Markdown)
    ↓
Ingestion Layer (SourcePlugin → parse & normalize)
    ↓
Storage Layer (SQLite, cross-source dedup)
    ↓                                      ↓
Enrichment (background)           Recommendation Engine
  TMDB, OpenLibrary, RAWG           ├── Scoring Pipeline (always runs)
  fills metadata gaps               └── Variety penalty (when enabled)
                                                ↓
                                    Interface Layer (CLI/Web) → User
```

## Configuration

`config/config.yaml` (git-ignored) holds the bootstrap: the `web` bind settings
and the `storage` paths, both read before the database opens. The web account
lives in the database instead, so no credential is needed here.
`config/example.yaml` is that template and nothing more. Everything else
resolves through
[global configuration precedence](#global-configuration-precedence), and sources
through [source configuration precedence](#source-configuration-precedence).

A legacy file may still carry `inputs` sources and secrets. Both migrate into the
database on boot, and a secret found there logs a deprecation warning. The legacy
shape, which the `source_configs` table now expresses:

```yaml
inputs:
  my_roms:
    plugin: roms
    paths: ["inputs/roms"]
    enabled: true
```

## Extension Points

**A data source**: create `src/ingestion/sources/<name>/` holding `<name>.py`
(the `SourcePlugin` subclass), `__init__.py` (a one-line re-export), `README.md`
and `test_<name>.py` with mocked APIs. `PluginRegistry` discovers it. See
[PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md).

**An enrichment provider**: the same layout under
`src/enrichment/providers/<name>/`, with rate limiting configured in the provider
class. `EnrichmentRegistry` discovers it, so no core file changes.

**A content type**: extend the `ContentType` enum, declare its fields in
`src/models/detail_fields.py`, add its detail table to the schema, register
that table and its columns in `_DETAIL_TABLE_COLUMNS` (`src/storage/merge.py`),
add type-specific recommendation logic, update the data models.

Do not skip the `_DETAIL_TABLE_COLUMNS` step. It is the source of
`ALLOWED_DETAIL_TABLES`, which the joined SELECT is built against at import, so
an unregistered table fails the import of `src.storage.sqlite_db` rather than
one save path.

## Technology Stack

Python 3.11 through 3.14, SQLite, FastAPI and Click. Tested with pytest,
checked with Black, MyPy strict and Ruff.

### Development Tooling (Claude Code)

Two plugins, configured in `.claude/settings.json`: **Pyright LSP** for real-time
type analysis, **Frontend Design** for UI component generation.

One review agent is committed under `.claude/agents/`:

| Agent | Covers |
|-------|--------|
| `parity-review` | Capabilities exposed in one interface but not the other |

It is native here deliberately: CLI/web parity is this repository's invariant,
and the agent enumerates this repository's capability surface.

## Container Artifacts

The deployment guide is [docs/DOCKER.md](docs/DOCKER.md). This is the shape.

One image, `ghcr.io/therealahall/recommendinator:VERSION`, carrying the
application, the frontend build and the Python dependencies. It publishes a
multi-arch manifest for `linux/amd64` and `linux/arm64`. `linux/arm/v7` is
unsupported because the Python wheel ecosystem is too thin there.

`Dockerfile` is multi-stage:

1. **frontend-builder**, `node:24-slim` running `pnpm build` into
   `src/web/static/dist/`.
2. **builder**, `python:3.11-slim` running `uv sync --locked` into `/app/.venv`.
3. **runtime**, `python:3.11-slim` with the `appuser` non-root account,
   application source, frontend dist, the venv and the entrypoint script. Its
   `ENTRYPOINT` is `/app/docker/entrypoint.sh` (which bootstraps `config.yaml`
   from `example.yaml` on first run), `CMD` starts uvicorn, and `HEALTHCHECK` is
   `python -m src.web.healthcheck`, which carries no credentials and counts an
   unauthenticated 401 as healthy.

`.github/workflows/docker.yml` builds it on `linux/amd64` for a `pull_request`,
without pushing, and smoke-tests it: it polls `/api/auth/session`, the one route
a signed-out caller may reach, then runs the image's own `HEALTHCHECK` inside it.

On a `v*` tag, `guard` refuses any tag that is not `vMAJOR.MINOR.PATCH` on a
commit main descends from, then decides which of `latest`, `X` and `X.Y` may
move: an alias stays put unless this release is the newest its scope names and
descends from every other release there. `verify` reruns the gate on the tagged
tree, and only then does `publish` build multi-arch, push `X.Y.Z` plus the
aliases the guard allowed, and attach provenance and SBOM attestations.

`.github/workflows/release.yml` cuts that tag, on `workflow_run` after CI
succeeds: only for a push to this repository, and only while the validated
commit is still main's tip. It uploads `docker-compose.yml` as a release asset.

## Security and Privacy

- All processing happens locally. External calls reach data source APIs (Steam,
  GOG, Epic, Sonarr, Radarr, Trakt) and enrichment APIs (TMDB, OpenLibrary,
  RAWG), and nothing else.
- The web interface is internal network only.
- API keys and OAuth tokens are stored encrypted in `credentials`. A secret
  placed in git-ignored `config/config.yaml` for bootstrap is swept into
  encrypted storage on startup.
- See [SECURITY.md](docs/SECURITY.md).

## Future Enhancements

- Discovery mode, surfacing things you did not know about
- Interactive refinement ("I'm burnt out on sci-fi")
