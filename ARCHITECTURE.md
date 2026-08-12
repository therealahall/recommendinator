# Architecture Documentation

## Overview

Recommendinator ingests from multiple sources and ranks recommendations through a
scoring pipeline. AI is optional. When enabled, a local LLM via Ollama adds
semantic similarity, natural language explanations, and chat.

## System Components

### 1. Data Ingestion (`src/ingestion/`)

Parses and normalizes data from external sources.

| Sources | Content |
|---------|---------|
| Goodreads CSV, Goodreads RSS shelves, The StoryGraph CSV, Calibre-Web OPDS | Books |
| Steam Web API, GOG OAuth, Epic via Legendary, ROM library scanner | Video games |
| Sonarr | TV shows |
| Radarr | Movies |
| Trakt device-code OAuth | TV shows and movies |
| Generic CSV, JSON, Markdown | Any |

- Plugins subclass `SourcePlugin` (`plugin_base.py`), auto-discovered from
  `src/ingestion/sources/` by `PluginRegistry`. Each validates its own config,
  fetches, and normalizes ratings.
- Sources are **named instances**: a user-defined id plus a `plugin:` naming the
  type, so one plugin can back several. `ContentItem.source` carries the id, not
  the plugin name. `resolve_inputs()` (`src/web/sync_sources.py`) resolves
  entries to `(source_id, plugin, config)`.
- `execute_multi_source_sync` is shared by CLI and web. Each enabled source runs
  on its own thread (`sync.max_workers`, default 4) and results keep input
  order. Rate limits are per source, so cross-source parallelism is safe.
- The web `SyncManager` aggregates progress callbacks into a `sources` map.
- Source config is writable over HTTP, so two field kinds are constrained rather
  than trusted. `paths.py` contains a file plugin's path inside
  `security.allowed_source_roots`, a config.yaml key the settings API cannot
  reach; `urls.py` checks a base URL's shape. A `credential_bound` `ConfigField`
  (a plugin's `url`, Calibre-Web's `verify_ssl`) clears the source's stored
  secrets when it changes.

### 2. Storage (`src/storage/`)

SQLite holds everything structured. ChromaDB holds vector embeddings and is
initialized only when AI is enabled.

| Table | Holds |
|-------|-------|
| `users` | Per-user settings (JSON) |
| `content_items` | Library items, scoped by `user_id` |
| `book_details`, `movie_details`, `tv_show_details`, `video_game_details` | Per-type detail |
| `credentials` | Encrypted OAuth tokens and API keys, per-source and global |
| `source_configs` | Non-sensitive per-source config |
| `settings` | Global config, dotted leaf key to JSON value, only what a user set |
| `enrichment_status` | Enrichment tracking |
| `core_memories`, `conversation_messages`, `preference_profiles` | Chat |

#### User-owned fields

`rating`, `review`, `status`, `date_completed` and `ignored` belong to the user.
Four methods on `SQLiteDB` write them.

`save_content_item` is the sync door:

| Field | Rule |
|-------|------|
| `rating`, `review` | Fill-only, written only into an empty column, so a re-import cannot erase either |
| `status` | Forward-only, through `resolve_status_forward` (`src/storage/merge.py`) |
| `date_completed` | Later date wins |
| `ignored` | Only a stated `True` or `False` wins, in either direction. `None` leaves the stored flag alone |

One exception to forward-only sits outside that resolution. After the upsert,
`_handle_tv_season_change` regresses a completed TV show to
`currently_consuming` when the sync raises its season count above the seasons the
user checked off, because new seasons mean the show is not finished. It needs an
existing `seasons_watched` list, and it skips ignored items.

A missing column, a blank CSV cell and a JSON `null` all reach storage as `None`
(`parse_ignored_field`, `src/ingestion/sources/generic_csv/generic_csv.py`). That
protects a hand-maintained file, not this project's exports.
`_item_to_export_dict` (`src/web/export.py`) states `true` or `false` on every
row, so re-importing an export replaces the ignore list wholesale with the state
at export time. Storage cannot tell a stated `false` from a plugin's defaulted
`False`, so a plugin states the flag only when its source does. See
[PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md#best-practices).

The three user-action doors overwrite freely and write only what the caller
supplied:

- **`complete_content_item`** backs the `complete` CLI command,
  `POST /api/complete` and chat's `mark_completed`. It finds or creates the row
  and applies rating, review, status and date in one transaction. `status` is
  written outright rather than resolved forward. Only chat can name a date, and a
  named date is written as given even when it precedes the stored one.
- **`update_item_from_ui`** backs the web edit modal, `library edit` and the chat
  tool executor. "Not supplied" is spelled three ways. `status` is required.
  `rating` and `review` use the `UNSET` sentinel, because `None` has to mean
  clear. `seasons_watched`, `genres`, `tags` and `description` use `None`, so for
  those the *empty* value is the clear: `[]` or `""`. `PATCH /api/items/{id}` can
  clear all four. The web dialog sends `null` for an emptied description, and the
  CLI cannot spell an empty list, so each surface reaches a different subset.
- **`set_item_ignored`** backs the Ignore buttons
  (`PATCH /api/items/{id}/ignore`) and `library ignore` / `library unignore`. It
  writes `ignored` alone.

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

Duplicate consolidation also writes all five, under the
[dedup rules](#cross-source-deduplication). It runs in the shared upsert, in
`SQLiteDB.deduplicate_items` (public, no production caller today), and in the
schema-migration merge, all through `merge_scalar_columns`
(`src/storage/merge.py`).

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
- `src/storage/credential_orphans.py` reads that same view for credentials left
  under a plugin name by an older release: every sync warns about one, and
  deleting the last source on that plugin deletes it.

**Item attribution.** `content_items.source` holds a source id. Six plugins once
dropped theirs, labelling rows with the plugin name, so a later sync split a
library in two. `migrate_source_attribution` (`src/storage/source_migration.py`)
moves those rows onto the single source running that plugin, and refuses when
two do, because nothing records which one they came from.

It reruns every boot until nothing is left for a later run to do, then records
itself in `completed_migrations`. Two of its three refusals name a config change
that resolves them, so those hold the record open. Each is logged once.
Messages:
[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#source-attribution-at-startup).

#### Global configuration precedence

**const default < YAML < database**, for `features`, `ollama`,
`recommendations`, `conversation`, `sync`, `enrichment`, `web` and `logging`.

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
database open, so the steps that cannot be repeated cheaply are guarded by
`PRAGMA user_version`: it reads the stored version once, runs each step whose
version the database is below, and writes `_SCHEMA_VERSION` back at the end,
inside the same transaction. Version 1 clears every pre-existing `settings` row
and version 2 prunes `_ORPHANED_SETTING_KEYS`, so anything a user sets
afterwards survives. Version 3 is `_repair_legacy_content_rows` — title
re-normalization, the stranded detail-shape repair and the duplicate merge —
three scans of the whole library that no current write path gives anything to
find. A database with no tables yet reports as already current, because
`CREATE TABLE` leaves nothing for any of them to repair.

Version 4 records the derived columns below and guards nothing. Their fill
selects the rows missing them and runs after the duplicate merge, which can
move a creator onto the row that survives, so it repairs a row a downgraded
build inserted into a database already stamped 4 rather than being spent on the
first open that sees one.

#### Derived sort and search columns

`content_items.sort_title` and `content_items.search_text`
(`src/storage/derived.py`) hold `get_sort_title(title)` and the item's
normalized title and creator, so `get_content_items` orders in SQL under a real
`LIMIT`/`OFFSET` and builds a `ContentItem` only for the rows it returns. Both
are recomputed from what is stored after every save and every dedup merge — the
creator column is fill-only, so they are read back from the row rather than
taken from the item being saved, and the creator is picked by content type the
way the read picks it. Every `ORDER BY` ends in `ci.id`, because SQL ordering is
not stable and a page boundary inside a tie repeats one row and drops another.

A search is one matched set. SQL orders the filtered candidates and projects
each as an `id` and its `search_text`; `search_text_matches` runs all three
tiers — exact, substring and the fuzzy window scan SQL cannot express — and the
page is sliced out of what matched, so no candidate that misses and no match
outside the page costs a `ContentItem`. The scan stops as soon as the page is
full, so a search carrying a limit never reads past it. Typo tolerance is never
conditional, and the pages of a search concatenate into the answer that search
gives unpaged. `search_text` joins its two halves with a character search
normalization can never produce, so a term never matches across the
title/creator boundary.

#### Cross-source deduplication

Items are deduplicated by normalized title. A save looks up
`(user_id, external_id, content_type)`, then merges any *different* row sharing
`(user_id, content_type, normalized_title)`. With no external_id match it falls
back to a direct normalized-title lookup. The version-3 migration re-normalizes
every title once and merges whatever that exposes.

Merge rules:

- `rating` and `review` fill from the duplicate only into a null
- `date_completed` keeps the later date
- `status` takes the further-advanced under the sync ordering
- `ignored` is the OR of both rows
- Genres and tags merge additively, monotonic columns (seasons, episodes) keep
  the higher value, and detail metadata merges existing-wins
- `seasons_watched_dates` is the exception, merged per season keeping the later
  watch date, so an ingestion date never overrides a user date

Consolidation deletes a row outright, so the `status` and `ignored` rules are
what stop it reverting a completion or un-ignoring an item.

#### Detail-shape repairs

`_migrate_stranded_detail_shapes`, in the same `create_schema` pass, rewrites
three shapes storage no longer writes and no re-sync corrects. A `total_seasons`
duplicated in a TV show's metadata blob moves onto the `seasons` column, taking
the higher of the two so the count is never lowered. It runs *before*
consolidation — inside the one transaction `create_schema` commits, so a failure
in either discards both — so each row folds its own stranded count onto its own
column and the merge above then weighs two real counts rather than whichever
blob copy survived it. A GOG game's `developers`/`publishers`, stranded in the
blob before either was an alias, fold onto the `developer` and `publisher`
columns as names, filling only a column that is empty; without that, the objects
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
`complete_content_item`, `save_credential` and
`merge_user_preference_config` all take it — the last because
`PUT /api/users/{id}/preferences` is a partial merge, and two of them at once
would otherwise each write a `users.settings` blob read before the other
landed, losing one wholesale.

### 3. LLM (`src/llm/`), optional

Talks to Ollama when AI is enabled, providing embeddings for similarity, natural
language explanations, and preference rule interpretation.

Flags: `features.ai_enabled` is the master toggle, and both
`features.embeddings_enabled` and `features.llm_reasoning_enabled` require it.

**All user text is sanitized before it reaches a prompt** (`src/utils/text.py`),
against prompt injection:

- `sanitize_prompt_text` strips newlines, control characters and injection
  markers, capping at 100 chars
- `sanitize_prompt_text_long` does the same with a configurable cap, for
  conversation history
- `sanitize_prompt_text_with_truncation` returns `(text, was_truncated)`, so a
  caller appends an ellipsis only on real truncation
- `_sanitize_genre` uses a stricter allowlist, capping at 50 chars

### 4. Recommendations (`src/recommendations/`)

A unified scoring pipeline that always runs, across content types. AI is an
enhancement on top of it.

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
  |     |-- [SemanticSimilarityScorer]  when AI enabled and the search found
  |     |                                something (dropped otherwise)
  |
  |-- UserPreferenceConfig (per-user weight overrides)
  |-- Variety penalty (variety.py) when variety_penalty > 0
  |-- [LLM reasoning] when AI enabled
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
  produces recommendations — scored, LLM-only, library fallback — returns
  `Recommendation`, and a path with nothing to say about references or blurbs
  says so with an empty default rather than a missing field. Both interfaces
  serialise it through `to_payload`, which is what keeps `recommend --format
  json` and `GET /api/recommendations` one document.
- The taste signal is completed items that are **rated** and **not ignored**,
  across all content types. Nothing else shapes preferences, scoring, similarity
  or explanations. Two consumption facts sit outside it and are answered from
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

**Cross-content-type matching works without AI.** Semantic genre clusters
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

### 5. Enrichment (`src/enrichment/`)

Background metadata gap-filling from external APIs. Providers subclass
`EnrichmentProvider`, auto-discovered from `src/enrichment/providers/`, each with
its own token-bucket rate limiter. A background worker runs them in configurable
batches, and an optional hook fires it after a sync.

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
- An item counts as enriched only with a real provider, no error, not
  `not_found`, and `needs_enrichment=0`. `get_content_items(enrichment=...)` and
  the per-row `enriched` flag share that predicate (`_ENRICHED_PREDICATE`).
- A run skips items it already attempted, so a queued failure is tried once per
  run.

### 6. Conversation (`src/conversation/`), optional

Requires AI. `ConversationEngine` orchestrates streaming responses over
`MemoryManager` (core memories), `ContextAssembler`, `ToolExecutor` (mark
completed, update rating, save memory), `IntentDetector` (`intent.py`),
`MemoryExtractor` and `ProfileGenerator`.

`IntentDetector` matches tool actions by regex before the LLM runs, and a
high-confidence match executes without invoking it.

`ContextAssembler` safeguards:

- Only the single highest-ranked item enters context, never a ranked list
- User messages are sanitized, assistant messages only length-truncated, which
  preserves LLM formatting
- Backlog items are tagged `[NOT YET CONSUMED]`, so the LLM cannot claim the user
  enjoyed them
- Only `COMPLETED` items appear under "Recently Completed"
- Match scores become qualitative labels through `_score_to_qualitative()`, never
  raw percentages

**Compact mode** (`conversation.context.compact_mode`) swaps in a condensed
system prompt of around 800 tokens, tighter context limits, compact item
formatting and pre-LLM intent detection. `ollama.conversation_model` can point
chat at a smaller model than recommendations use. See
[MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md).

### 7. Interfaces

The CLI and the web UI are **alternative interfaces to the same capabilities**,
neither a subset of the other. Both call the same recommendation, ingestion,
storage and conversation services. New capabilities land in both, and
`parity-review` enforces that on any change under `src/web/` or `src/cli/`.

**CLI** (`src/cli/`): Click groups `status`, `recommend`, `update`, `complete`,
`source`, `settings`, `preferences`, `enrichment`, `library`, `auth`, `memory`,
`profile`, `chat`, most carrying a `--format json` view. Full reference in
[docs/CLI.md](docs/CLI.md).

**Web** (`src/web/` + `resources/`): a FastAPI REST backend and a Vue 3 SPA with
Tailwind v4, built by Vite from `resources/js/` and `resources/css/` into
`src/web/static/dist/` with content-hashed filenames. Tabs are Recommendations,
Library, Chat, Data, Preferences and Settings, with Chat hidden when AI is off.
SSE streams chat responses and recommendation blurbs. Internal network only.

- The **Settings** page is the UI peer of the `settings` CLI group, over the
  shared `src/settings/service.py`. Infra and security leaves
  (`web.allowed_origins`, `logging.*`) sit in an **Advanced** group badged
  **restart required**. Provider secrets get masked write-only controls.
- Recommendation cards **ignore** or **mark complete**, each removing the card
  without regenerating the list.
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
  and a `colors.css`. Tailwind `@theme` maps the vars to utilities, and
  `color-mix()` means a theme defines only core colors. Selection persists per
  user, defaulting to `nord`. See
  [THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md).
- The UI polls `GET /api/status` every 5 minutes and banners a newer server
  version.
- Library export: `GET /api/items/export?type=book&format=csv`.
- A component a handler **requires** is a parameter of it, annotated with one
  of the `Required*` aliases in `src/web/guards.py` (`RequiredStorage`,
  `RequiredConfig`, `RequiredEngine`, and the chat pair). Absent, it answers
  **503**, never 500, with one message per dependency, so one server state gets
  one status code and one message on every route. Declaring it in the signature
  rather than calling a guard in the body means it cannot be forgotten while
  the handler still compiles, FastAPI caches it so a handler and its own
  dependencies acquire it once, and it resolves **before** request validation —
  an invalid request to an endpoint whose component is down answers 503, not
  422. The chat engine is the one component a running server is actually
  without, when the LLM is disabled — `create_app` populates the others or
  raises, so their guards hold that invariant.
- Every `/api` handler is plain `def`, so Starlette runs all of them in a
  threadpool: they do blocking SQLite, scoring and outbound OAuth work with
  nothing to await, and on the event loop one of them stalled every other
  request for its whole duration. That threadpool is anyio's, capped at **40
  tokens**, and a streaming endpoint holds a token for the duration of each
  generator step — so enough concurrent long streams is where the API stops
  answering, and it is the first thing to check when it does. The config
  watcher is not a handler and hands its reload to a worker thread for the same
  reason.
- The two SSE endpoints (`GET /api/recommendations/stream` and `POST /api/chat`)
  share one budget of `MAX_CONCURRENT_STREAMS` slots in
  `src/web/stream_limit.py` and answer **503** past it, or a handful of forgotten
  tabs spends those 40 tokens. The slot is taken in the handler, before the
  response starts, and released when the generator finishes or the client
  disconnects.
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
  lock too. Discovery used to clear and refill the live map, so a threadpool
  worker reading mid-pass — or a second pass — left the registry flagged
  discovered over a partial map for the life of the process: 404 for a source
  that exists, and a sync of "all" reporting success having skipped it. A pass
  now fills a map of its own and swaps it in. The scanning stays *outside* the
  lock, because importing a module and constructing a plugin both run
  third-party code from `private/plugins/`, and a plugin calling
  `get_registry()` under the lock would hang the process with nothing logged.
  The cost is that a re-entering plugin reads the pre-pass map, and that two
  cold passes each do the work; both publish complete maps and the later swap
  wins.
- The lazily-built process singletons behind `/api/sync/*` and
  `/api/enrichment/*` (`get_sync_manager`, `get_enrichment_manager`) build under
  a module-level lock for the same reason: two threadpool requests on a cold
  process would otherwise each get a manager of their own, and a job started
  through one is invisible to the status endpoint reading the other.
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
Storage Layer (SQLite, cross-source dedup, ChromaDB when AI is enabled)
    ↓                                      ↓
Enrichment (background)           Recommendation Engine
  TMDB, OpenLibrary, RAWG           ├── Scoring Pipeline (always runs)
  fills metadata gaps                ├── [AI: vector similarity]  ← optional
                                     ├── Variety penalty          ← when enabled
                                     └── [AI: LLM reasoning]     ← optional
                                                ↓
                                    Interface Layer (CLI/Web) → User
                                                ↓
                                    [Conversation System]  ← optional, AI-only
                                      Chat, memory, tools
```

## Configuration

`config/config.yaml` (git-ignored) holds the bootstrap: the `web` bind settings,
`web.api_token`, and the `storage` paths, all read before the database opens. The
token is deliberately not a settings leaf — it guards the API the Settings page
is served over — and boot fails without it. `config/example.yaml` is that
template and nothing more. Everything else resolves through
[global configuration precedence](#global-configuration-precedence), and sources
through [source configuration precedence](#source-configuration-precedence).

A legacy file may still carry `inputs` sources and secrets. Both migrate into the
database on boot, and a secret found there logs a deprecation warning. The legacy
shape, which the `source_configs` table now expresses:

```yaml
inputs:
  my_books:
    plugin: json_import
    path: "inputs/books.json"
    content_type: "book"
    enabled: true
```

## Extension Points

**A data source**: create `src/ingestion/sources/<name>/` holding `<name>.py`
(the `SourcePlugin` subclass), `__init__.py` (a one-line re-export), `README.md`
and `test_<name>.py` with mocked APIs. `PluginRegistry` discovers it. See
[PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md).

**An enrichment provider**: the same layout under
`src/enrichment/providers/<name>/`, with rate limiting configured in the provider
class. `EnrichmentRegistry` discovers it.

**A content type**: extend the `ContentType` enum, declare its fields in
`src/models/detail_fields.py`, add its detail table to the schema, register
that table and its columns in `_DETAIL_TABLE_COLUMNS` (`src/storage/merge.py`),
add type-specific recommendation logic, update the data models.

Do not skip the `_DETAIL_TABLE_COLUMNS` step. It is the source of
`ALLOWED_DETAIL_TABLES`, which the joined SELECT is built against at import, so
an unregistered table fails the import of `src.storage.sqlite_db` rather than
one save path.

## Technology Stack

Python 3.11, SQLite, FastAPI, Click, Ollama (local, AMD-compatible) and
ChromaDB (optional, AI only). Tested with pytest, checked with Black, MyPy strict
and Ruff.

### Development Tooling (Claude Code)

Two plugins, configured in `.claude/settings.json`: **Pyright LSP** for real-time
type analysis, **Frontend Design** for UI component generation.

Seven review agents, all committed under `.claude/agents/`:

| Agent | Covers |
|-------|--------|
| `security-review` | Credential exposure, injection, CORS, the rules in `docs/SECURITY.md` |
| `code-review` | Dead code, DRY, naming, type safety, over/under-engineering |
| `test-review` | Coverage, mock hygiene, regression test format, edge cases |
| `document-review` | Staleness, cross-document consistency, missing documentation |
| `accessibility-review` | WCAG 2.1 AA for frontend code, approving backend-only diffs immediately |
| `commit-hygiene` | Atomic commit structure, conventional format, message quality |
| `parity-review` | Capabilities exposed in one interface but not the other |

The first six are project-agnostic and shared across repositories, so their
canonical source lives outside this repository and the committed copies are what
a checkout gets. They read `CLAUDE.md` and `docs/` for this project's rules.
`parity-review` is native here.

## Container Artifacts

The deployment guide is [docs/DOCKER.md](docs/DOCKER.md). This is the shape.

| Variant | Image | Contents |
|---------|-------|----------|
| Default | `ghcr.io/therealahall/recommendinator:VERSION` | Application, frontend build, base Python deps |
| AI | `ghcr.io/therealahall/recommendinator:VERSION-ai` | Default plus the `ai` extras, `ollama` and `chromadb` |
| Ollama sidecar | `ghcr.io/therealahall/recommendinator-ollama:VERSION` | `ollama/ollama` plus the model-pull entrypoint, required by the AI variant |

All three publish multi-arch manifests for `linux/amd64` and `linux/arm64`.
`linux/arm/v7` is unsupported because the Python 3.11 wheel ecosystem, ChromaDB
especially, is too thin there.

`Dockerfile` is multi-stage with two targets:

1. **frontend-builder**, `node:20-slim` running `pnpm build` into
   `src/web/static/dist/`.
2. **builder-base** → **builder-default** / **builder-ai**, `python:3.11-slim`
   running `uv sync --locked` (plus `--extra ai`) into `/app/.venv`.
3. **runtime-base**, `python:3.11-slim` with the `appuser` non-root account,
   application source, frontend dist and the entrypoint script.
4. **default** / **ai**, copying the right venv, setting `ENTRYPOINT` to
   `/app/docker/entrypoint.sh` (which bootstraps `config.yaml` from
   `example.yaml` on first run), starting uvicorn through `CMD`, and setting
   `HEALTHCHECK` to `python -m src.web.healthcheck`, which reads no token and
   counts an unauthenticated 401 as healthy.

`docker/Dockerfile.ollama` extends `ollama/ollama` with the model-pull
entrypoint, a non-root `ollama` user whose home and model store are
`/var/lib/ollama`, and a `HEALTHCHECK` that holds `app-ai` back until the first
pull lands.

`.github/workflows/docker.yml` builds all three on `linux/amd64` for a
`pull_request`, without pushing, and smoke-tests the default variant: it seeds
a token into a mounted config, polls the authenticated `/api/status`, then runs
the image's own `HEALTHCHECK` inside it.

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
  GOG, Epic, Sonarr, Radarr, Trakt), enrichment APIs (TMDB, OpenLibrary, RAWG)
  and a local Ollama, and nothing else.
- The web interface is internal network only.
- API keys and OAuth tokens are stored encrypted in `credentials`. A secret
  placed in git-ignored `config/config.yaml` for bootstrap is swept into
  encrypted storage on startup.
- See [SECURITY.md](docs/SECURITY.md).

## Future Enhancements

- Discovery mode, surfacing things you did not know about
- Interactive refinement ("I'm burnt out on sci-fi")
- Scheduled sync, cron-style
