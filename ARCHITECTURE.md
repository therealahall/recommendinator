# Architecture Documentation

## Overview

Recommendinator ingests from multiple sources and ranks recommendations through a
scoring pipeline. Everything runs on the local machine, and every score is
arithmetic a reader can follow back to the library it came from.

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
| `sync_runs` | One row per sync run: outcome, item counts and errors, pruned per source |
| `preference_profiles` | The generated per-user taste profile |

#### User-owned fields

`rating`, `review`, `status`, `date_completed` and `ignored` belong to the user.
Five methods on `SQLiteDB` write them.

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
existing `seasons_watched` list, and it skips ignored items. A stored season
count that is unknown or zero gives it nothing to compare against, so no
regression happens there either.

The enrichment door runs that same pass:

- **`save_enrichment_metadata`** writes a provider's metadata to the detail
  table and the derived columns. Of the user-owned fields it writes only
  `status`, and only through the season regression above.

A missing column, a blank CSV cell and a JSON `null` all reach storage as `None`
(`parse_ignored_field`, `src/ingestion/importers/rows.py`). That
protects a hand-maintained file, not this project's exports.
`_item_to_export_dict` (`src/utils/export.py`) states `true` or `false` on every
row, so re-importing an export replaces the ignore list wholesale with the state
at export time. Storage cannot tell a stated `false` from a plugin's defaulted
`False`, so a plugin states the flag only when its source does. See
[PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md#best-practices).

The three user-action doors overwrite freely and write only what the caller
supplied:

- **`complete_content_item`** backs the `complete` CLI command and
  `POST /api/complete`. It finds or creates the row and applies rating, review,
  status and date in one transaction. `status` is written outright rather than
  resolved forward. A named date is written as given even when it precedes the
  stored one.
- **`update_item_from_ui`** backs the web edit modal and `library edit`.
  "Not supplied" is spelled two ways. `status`, `rating` and `review` use the
  `UNSET` sentinel, because for the last two `None` has to mean clear.
  `seasons_watched`, `genres`, `tags` and `description` use `None`, so there the
  *empty* value clears: `[]` or `""`. `PATCH /api/items/{id}` can clear all four,
  and so can `library edit`: `--clear-seasons`, `--clear-genres`, `--clear-tags`
  and `--description ""`.
- **`set_item_ignored`** backs the Ignore buttons
  (`PATCH /api/items/{id}/ignore`) and `library ignore` / `library unignore`. It
  writes `ignored` alone.

For a TV show, an omitted status is derived from `seasons_watched` in
`src/utils/series.py`, and a stated `completed` ticks every season unless the
total is unknown. No status empties the list: a Trakt sync and a CSV/JSON import
both write watched seasons, and the dialog hides its checklist for a show whose
total never synced, so that show's status-only save would erase them unseen.
Both supplied are written as given.

**No door stores a blank review.** A stored `""` reads as a review the user wrote
and blocks every later import from filling the field. The sync door declines to
fill from one, `_write_completion` drops one, `update_item_from_ui` clears the
column, and `complete --review`, `POST /api/complete` and
`PATCH /api/items/{id}` all refuse one outright.

**`date_completed` is never replaced silently.** A completion carrying no date
fills an empty column with today and keeps an existing date. A named date is
written as given, but no further ahead than `MAX_COMPLETION_DATE_SKEW` — one
day, for a caller in a zone ahead of the server — and the check is at the door,
so no surface can skip it.
`update_item_from_ui` stamps a date only on a transition *into* `completed` on an
undated row, so an unrelated edit cannot date a years-old import as finished
today. Dates are the host's local calendar day rather than UTC (`local_today`,
`local_date_from_iso_timestamp`), and a container takes its zone from `TZ`. See
[DOCKER.md](docs/DOCKER.md#environment-variables). The
[variety ladder](docs/SCORING.md#variety-after-completion) orders completions by
this date.

The merge door, `merge_content_items`, also writes all five, under the
[dedup rules](#cross-source-deduplication) and through `merge_scalar_columns`
(`src/storage/merge.py`). It is the only door that can be undone.

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
`normalized_title` backfill and the stranded detail-shape repair. Version 16
reduces a list column holding an object and clears a re-queued item's stale
quality. Version 17 moves a book title's `(Series, #N)` marker into its
metadata, clears a placeholder author, folds a stranded company name,
re-normalizes every title and clears the derived columns for the backfill.

No step merges or unmerges rows: an upgraded library rewrites its keys and
leaves the merge door to decide the rest.

Versions 4, 5 and 7 to 15 record a shape rather than guarding a step; the
derived-column fill and version 17's re-normalize cover the rows they left.

The one table rebuild, `_move_external_ids_off_content_items`, guards on the
`content_items.external_id` column rather than the version, since it commits
before the stamp. `_assert_no_child_followed` aborts it when SQLite ignores the
`legacy_alter_table` pragma, rather than commit a database no write can use.

#### Derived sort and search columns

`content_items.sort_title` and `content_items.search_text`
(`src/storage/derived.py`) hold `get_sort_title(title)` and the item's
normalized title, creator and series, so `get_content_items` orders in SQL
under a real `LIMIT`/`OFFSET` and builds a `ContentItem` only for the rows it
returns. Both are recomputed from what is stored after every save and every
merge — the creator column is fill-only, so they are read back from the row
rather than taken from the item being saved, and the creator is picked by
content type the way the read picks it. Every `ORDER BY` ends in `ci.id`,
because SQL ordering is not stable and a page boundary inside a tie repeats one
row and drops another.

A search is one matched set. SQL orders the filtered candidates and projects
each as an `id` and its `search_text`; `search_text_matches` runs all three
tiers — exact, substring and the fuzzy window scan SQL cannot express — and the
page is sliced out of what matched, so no candidate that misses and no match
outside the page costs a `ContentItem`. The scan stops as soon as the page is
full, so a search carrying a limit never reads past it. Typo tolerance is never
conditional, and the pages of a search concatenate into the answer that search
gives unpaged. `search_text` joins its parts with a character search
normalization can never produce, so a term never matches across the boundary
between a title, its creator and the series it states.

#### Cross-source deduplication

A save looks up the item its source knows by `(source, external_id)`, falling
back to the normalized title, oldest row first. It skips a row whose merge
group holds another id from that source: a source lists an item once, so two
of its ids are two items.

The key is deliberately lossy — a region qualifier, an edition and a trailing
year all leave it — and three vetoes make it safe. A creator, a
year or a region both rows state and disagree on refuses the match, as does a
year one row spells into its title against a row stating none. Books are
exempt: their `year_published` is the edition's. Where the key names two rows,
only one spelled as the incoming title is taken.

Comparison is core's and source shape is the plugin's. Core sees two rows and
decides whether they name one work; only a plugin knows what its own source
appends to a title or writes where it has no value. So a shelf emits `title`
beside `metadata["series"]` rather than "All Systems Red (The Murderbot
Diaries, #1)", and the save door refuses the "Unknown" Calibre-Web sends for an
authorless book, once for every source. Schema 17 splits what earlier builds
stored, re-keys every title and re-derives every row's sort and search columns.

It updates the matched row in place and absorbs nothing: absorbing the
same-titled rows beside an id match destroyed ids a third source held. A pair
predating both ids stays two rows until the merge door below joins them.

External ids live in `content_item_external_ids`, one per source per item:
Steam's app 440 and GOG's product 440 are different games. Either path records
the incoming `(source, external_id)`, which makes a merge survive the losing
source's next sync.

`content_items.source` is display provenance and no sync overwrites it. A read
reports every `(source, external_id)` pair the item holds, which both interfaces
carry as `external_ids`; `ContentItem.id` stays the id of the item's own source,
because a save keys on it.

The version-8 migration rebuilds `content_items` to drop the id column and the
`(user_id, external_id, content_type)` key it sat under. Every id it held is
filed under the source `(legacy)`, which no source id can spell: the column
beside those ids named the last source to sync the row, not the id's owner, so
each source attaches its own on its next sync and matches by title until then.

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
an enrichment run that landed since where it stands. It holds the survivor
whole, so merges undo newest first, refusing any other order, and
`list_content_item_merges` lists them in it.

Enrichment merges too: a survivor still queued takes the absorbed row's settled
outcome, so absorbing an enriched item never re-queues it. Both save lookups
resolve one hop through `merged_into`, so the survivor reports the group's ids
and absorbing a row that has absorbed one brings it up, keeping the hop single.

#### Detail-shape repairs

`_migrate_stranded_detail_shapes`, in the same `create_schema` pass, rewrites
three shapes storage no longer writes and no re-sync corrects. A `total_seasons`
duplicated in a TV show's metadata blob moves onto the `seasons` column, taking
the higher of the two so the count is never lowered. It runs inside the one
transaction `create_schema` commits, so a failure after it discards it.
A GOG game's `developers`/`publishers`, stranded in the
blob by a build that spelled them so, fold onto the `developer` and `publisher`
columns as names, filling only a column that is empty; schema 17 runs that fold
again, because neither plural is an accepted spelling any more. Without it, the
objects
GOG uses on some products make every later save of the item raise. GOG's old
per-platform flag dict becomes the list of names every
other producer writes — or nothing, since the dict was truthy even when it named
no supported platform — and only a dict whose values are all booleans is read as
flags, so an imported object such as `{"name": "PC"}` is left alone. Rows already
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
panel's rows and their weights reproduce the number displayed beside them. The
variety penalty is the only factor applied afterwards, and it has its own row.
See [docs/SCORING.md](docs/SCORING.md).

**Weights resolve const default < `config.yaml` < `settings` table < per-user.**
The first three assemble into the effective global. A per-user override
(`users.settings` JSON, `"preference_config"`) then wins per key, and an unset key
keeps the global. `min_rating_for_preference` and the counts have no per-user
field. The engine asks for the running config once per request and resolves the
weights, `min_rating_for_preference` and the custom-rule weight from that one
read, so a Settings-page edit reaches the next set of recommendations without a
restart. It asks rather than holds because scoring and config writes run in
different threadpool workers, so nothing keeps them apart in time: a hot-reload
binds a whole new config in one statement, and a save publishes all of its
live-applied leaves as one swap per section. A request in flight therefore
scores on the configuration it read at its start, whole, and a save landing
mid-request reaches the next one instead.

Invariants:

- The engine's output is one declared record (`record.py`). Every path that
  produces recommendations — scored or library fallback — returns
  `Recommendation`, and a path with nothing to say about references says so with
  an empty default rather than a missing field. Both interfaces
  serialise it through `to_payload`, which is what keeps `recommend --format
  json` and `GET /api/recommendations` one document.
- The taste signal is completed items that are **rated** and **not ignored**,
  across all content types. Nothing else shapes preferences or scoring. Two
  consumption facts sit outside it and are answered from
  wider sets: series ordering, from the full completed set, and the variety
  penalty below, which multiplies the final score rather than contributing to
  it.
- Ignored items are filtered from the candidate pool at fetch time, as they are
  from the signal set. Consumed items are excluded too.
- Series filtering with substitution (`series_in_order`) replaces a candidate
  failing the ordering rules with the earliest recommendable entry in its series,
  scored on its own merits, once per series.
- The variety penalty multiplies a candidate's final score by `1 - penalty`,
  taking the strongest penalty among its recently finished genre clusters. The
  ladder is built from the completions **of the recommended content type**
  (`get_consumption_items`: everything consumed or in progress that is not
  ignored, rated or not, which the ladder then narrows to completion events),
  because finishing something tires you of its genre whether or not you rated
  it. An ignored completion claims no rung. Its top rung is the user's
  `variety_penalty` over the `5.0` maximum, clamped into `[0, 1]` by
  `top_penalty_for_preference` so no candidate is penalised past zero. A
  finished season of an ongoing show counts as a completion, dated by that
  season's watch timestamp. An active series continuation takes 60% of the
  penalty (`is_active_series_continuation`). See
  [SCORING.md](docs/SCORING.md#variety-after-completion).

**Cross-content-type matching is a lookup, not a model.** Genre clusters
(`genre_clusters.py`) map raw genre and tag terms onto a fixed set of thematic
clusters, so a book tagged "space warfare" reaches a TV show tagged "war".
Compound terms like "Sci-Fi & Fantasy" split first (`genre_normalizer.py`).
Cross-type reference items use cluster overlap rather than raw Jaccard, so
broadly-matching items cannot dominate.

**Adaptations and reference items are matched through an index**
(`reference_index.py`), built once per request over the taste signal. The
adaptation lookup runs once per candidate, before scoring. The reference lookup
runs after the slice, once per emitted recommendation, because nothing but those
records reads its result. Each signal item's normalized title, genres, thematic
clusters, creator and series name are derived when the index is built, and a
candidate reaches its matches by lookup: an adaptation by normalized title or
author, a reference by shared genre within its own content type or by shared
cluster across types. That matters because TV candidates are season-expanded, so
a few hundred shows become a few thousand candidates. The one thing no lookup
reaches is a same-type item the user rated 4+ with nothing else in common, which
qualifies as a reference on its rating alone, so each type also keeps its highly
rated items in signal order to fill the slots the lookup leaves empty.

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
  other 4xx would be rejected identically every run and is not retryable. One
  retryable failure keeps the item queued. Failures that are all non-retryable
  settle it as `not_found`.
- **A provider that keeps rejecting is abandoned for the run.** Five consecutive
  non-retryable rejections (`_MAX_CONSECUTIVE_REJECTIONS`) drop it, and the run
  ends once nothing unabandoned is left for its content type. Items no remaining
  provider reached are left queued and unwritten, and the run reports neither
  completed nor cancelled.
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
Epic and Trakt OAuth) and `src/utils/export.py`. So `parity-review` reviews every
change but those under `docs/`, `tests/`, tooling and themes: the capability
surface is all of `src/` and `resources/`.

Neither interface package imports the other, and each
framework stays in the package it serves: `fastapi` and `starlette` only under
`src/web/`, `click` only under `src/cli/`.

**CLI** (`src/cli/`): Click groups `status`, `recommend`, `update`, `complete`,
`source`, `settings`, `preferences`, `enrichment`, `library`, `auth`, `account`,
`profile`, most carrying a `--format json` view. One module
each under `src/cli/commands/`, re-exported from its `__init__` for
`src/cli/main.py`;
`src/cli/_shared.py` holds what more than one group uses. Full reference in
[docs/CLI.md](docs/CLI.md).

**Web** (`src/web/` + `resources/`): a FastAPI REST backend and a Vue 3 SPA,
built by Vite from `resources/js/` and `resources/css/` into
`src/web/static/dist/` with content-hashed filenames. Tabs are Recommendations,
Library, Duplicates, Data, Preferences and Settings. Internal network only.

- The **Settings** page is the UI peer of the `settings` CLI group, over the
  shared `src/settings/service.py`. Infra and security leaves
  (`web.allowed_origins`, `logging.*`) sit in an **Advanced** group badged
  **restart required**. Provider secrets get masked write-only controls.
- Recommendation cards **ignore** or **mark complete**, each removing the card
  without regenerating the list.
- The **Preferences** page carries the generated taste profile, the UI peer of
  `profile show` and `profile regenerate`.
- **Library search is bounded at 200 characters in four places**
  (`MAX_SEARCH_LENGTH` in `src/utils/sorting.py`, mirrored in
  `resources/js/constants/library.ts`), because a candidate matching neither the
  exact nor the substring tier costs a fuzzy-match window slid across it.
  `GET /api/items` answers 422, `library list --search` errors, the input caps
  typing and announces the cap to screen readers, and the library store
  truncates in `setFilter`. Within the bound, titles normalize on Python's `\w`,
  which spans every script. An ASCII-only class normalizes a Cyrillic or
  Japanese title to the empty string and makes it unreachable.
- Themes are folder-per-theme in `src/web/static/themes/`, each a `theme.json`
  and a `colors.css` overriding the `:root` vars `base.css` declares.
  `color-mix()` means a theme defines only core colors. Selection persists per
  user, defaulting to `nord`. See
  [THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md).
- The UI polls `GET /api/status` every 5 minutes and banners a newer server
  version.
- Library export: `GET /api/items/export?type=book&format=csv`. `type` is
  optional on both interfaces and omitting it exports every content type, under
  a header carrying all four types' columns.
- The duplicates review reaches the merge door from both sides: `/api/duplicates`
  and `/api/merges` behind the **Duplicates** page, `library duplicates`,
  `merge`, `unmerge`, `merges`, `decline-duplicate`, `declined-duplicates` and
  `undecline-duplicate` on the CLI. Both serialize through
  `src/utils/duplicate_serialization.py`. Neither offers to delete a row: every
  id they show is one a merge can hide, and deleting a hidden row would take its
  children with it and leave no undo.
- `sync_scheduler` (`src/web/scheduler.py`) runs on the app's lifespan, ticking
  once a minute and starting one due source, so a backlog staggers instead of
  opening a thread per source. No server, no scheduled sync. It and
  `POST /api/update` build the job through the same `build_sync_job`
  (`src/web/sync_dispatch.py`), so a scheduled run records and enriches as a
  requested one does. The run over every source overlaps nothing: the tick skips
  while it runs, and `POST /api/update` answers **409** either way.
- A component a handler **requires** is a parameter of it, annotated with one
  of the `Required*` aliases in `src/web/guards.py` (`RequiredStorage`,
  `RequiredConfig`, `RequiredEngine`). Absent, it answers
  **503**, never 500, with one message per dependency, so one server state gets
  one status code and one message on every route. Declaring it in the signature
  rather than calling a guard in the body means it cannot be forgotten while
  the handler still compiles, FastAPI caches it so a handler and its own
  dependencies acquire it once, and it resolves **before** request validation —
  an invalid request to an endpoint whose component is down answers 503, not
  422. `create_app` populates every component or raises, so the guards hold that
  invariant rather than describing a state a served request meets.
- Every `/api` handler is plain `def`, so Starlette runs all of them in a
  threadpool: they do blocking SQLite, scoring and outbound OAuth work with
  nothing to await, and on the event loop one of them stalled every other
  request for its whole duration. That threadpool is anyio's, capped at **40
  tokens**, so enough concurrent slow requests is where the API stops answering,
  and it is the first thing to check when it does. The config watcher is not a
  handler and hands its reload to a worker thread for the same reason.
- Writing the running config is serialised by one lock in `src/web/state.py`,
  not by the event loop. Four paths write it — `PUT /api/settings`,
  `DELETE /api/settings/{key}`, `POST /api/config/reload` and the config
  watcher — and each is a read-copy-store, so a reload rebinding
  `app_state.config` mid-save would leave the save publishing into a dict
  nobody reads any more: database and running config disagreeing with no error
  anywhere. The two writers reach the config through `writable_config()` rather
  than their `RequiredConfig` dependency, because the binding has to be
  resolved *inside* the lock — a dependency resolves and is released before the
  handler body runs. `RequiredConfig` stays on those routes as the 503 guard.
- The plugin and enrichment-provider registries publish under a module-level
  lock too. A pass used to clear and refill the live map, so a threadpool
  worker reading mid-pass left the registry flagged discovered over a partial
  map for good: 404 for a source that exists. A pass now fills a map of its own
  and swaps it in. Building it stays *outside* the lock, because a
  `private/plugins/` plugin calling `get_registry()` under it would hang the
  process silently.
- The lazily-built process singleton behind `/api/sync/*` (`get_sync_manager`)
  builds under a module-level lock for the same reason: two threadpool requests
  on a cold process would otherwise each get a manager of their own, and a job
  started through one is invisible to the status endpoint reading the other.
  `/api/enrichment/*` needs no singleton — its job lives in the shared
  `enrichment_job` record, so each handler reads or claims that record directly.
- A handler that does **not** require a component reads it through the
  unguarded `get_*` accessors in `src/web/state.py` and falls back when it is
  `None`, still answering 200: recommendations serve on the engine alone,
  `POST /api/complete` falls back to the registered feature-flag defaults, and
  `GET /api/status` guards none of the four it reads, because reporting which
  are down is its job. In `tests/test_web_api.py`,
  `TestUnguardedReadsAreOptional` pins the first two and
  `TestDependencyGuards.test_status_reports_initializing_when_components_are_down`
  pins the third, so a new handler picks one or the other: guard the read, or
  make the fallback real.

Dev server: Vite on `:5173` proxies `/api/*` and `/static/themes/*` to FastAPI on
`:18473`. Ports, proxy target and HMR client settings default to those and take
`DEV_SERVER_*` overrides. See `resources/vite/devServer.ts` and
[CONTRIBUTING.md](CONTRIBUTING.md).

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

Python 3.11, SQLite, FastAPI and Click. Tested with pytest, checked with Black,
MyPy strict and Ruff.

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
unsupported because the Python 3.11 wheel ecosystem is too thin there.

`Dockerfile` is multi-stage:

1. **frontend-builder**, `node:20-slim` running `pnpm build` into
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
- Scheduled sync, cron-style
