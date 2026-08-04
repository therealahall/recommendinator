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
fills an empty column with today and keeps an existing date.
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

**Settings-table migrations.** `_migrate_settings_table`
(`src/storage/schema.py`), called from `create_schema`, is guarded by
`PRAGMA user_version`. Version 1 clears every pre-existing row and version 2
prunes `_ORPHANED_SETTING_KEYS`. The guard advances the version, so anything a
user sets afterwards survives.

#### Cross-source deduplication

Items are deduplicated by normalized title. A save looks up
`(user_id, external_id, content_type)`, then merges any *different* row sharing
`(user_id, content_type, normalized_title)`. With no external_id match it falls
back to a direct normalized-title lookup. Schema migrations re-normalize every
title and merge whatever that exposes.

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
`complete_content_item` and `save_credential` all take it.

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
  |-- ScoringPipeline (always runs)
  |     |-- GenreMatchScorer        genre preference
  |     |-- CreatorMatchScorer      author/director/creators/developer
  |     |-- TagOverlapScorer        threshold and cluster tag overlap
  |     |-- SeriesOrderScorer       next in sequence
  |     |-- RatingPatternScorer     rating history in matching genres
  |     |-- ContentLengthScorer     soft penalty for length mismatch
  |     |-- ContinuationScorer      actively consumed items (dropped when none exist)
  |     |-- SeriesAffinityScorer    well-rated franchises (avg >= 4)
  |     |-- CustomPreferenceScorer  natural language rules
  |     |-- [SemanticSimilarityScorer]  when AI enabled
  |
  |-- UserPreferenceConfig (per-user weight overrides, diversity_weight)
  |-- Ranker (adaptation, series, diversity, preference adjustments)
  |-- Variety penalty (variety.py) when variety_penalty > 0
  |-- [LLM reasoning] when AI enabled
```

**Weights resolve const default < `config.yaml` < `settings` table < per-user.**
The first three assemble into the effective global. A per-user override
(`users.settings` JSON, `"preference_config"`) then wins per key, and an unset key
keeps the global. `min_rating_for_preference` and the counts have no per-user
field.

Invariants:

- The taste signal is completed items that are **rated** and **not ignored**,
  across all content types. Nothing else shapes preferences, scoring, similarity
  or explanations.
- Ignored items are filtered from the candidate pool at fetch time, as they are
  from the signal set. Consumed items are excluded too.
- Series filtering with substitution (`series_in_order`) replaces a candidate
  failing the ordering rules with the earliest recommendable entry in its series,
  scored on its own merits, once per series.
- The variety penalty multiplies a candidate's final score by `1 - penalty`,
  taking the strongest penalty among its recently finished genre clusters. The
  ladder is built from completions **of the recommended content type**, its top
  rung the user's `variety_penalty` over the `5.0` maximum. A finished season of
  an ongoing show counts as a completion, dated by that season's watch timestamp.
  The penalty halves for an active series continuation
  (`is_active_series_continuation`). See
  [SCORING.md](docs/SCORING.md#variety-after-completion).

**Cross-content-type matching works without AI.** Semantic genre clusters
(`genre_clusters.py`) map raw genre and tag terms onto a fixed set of thematic
clusters, so a book tagged "space warfare" reaches a TV show tagged "war".
Compound terms like "Sci-Fi & Fantasy" split first (`genre_normalizer.py`).
Cross-type reference items use cluster overlap rather than raw Jaccard, so
broadly-matching items cannot dominate.

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
  `resources/js/constants/library.ts`), because the term is fuzzy-matched per
  candidate over the whole library. `GET /api/items` answers 422,
  `library list --search` errors, the input caps typing and announces the cap to
  screen readers, and the library store truncates in `setFilter`. Within the
  bound, titles normalize on Python's `\w`, which spans every script. An
  ASCII-only class normalizes a Cyrillic or Japanese title to the empty string
  and makes it unreachable.
- Themes are folder-per-theme in `src/web/static/themes/`, each a `theme.json`
  and a `colors.css`. Tailwind `@theme` maps the vars to utilities, and
  `color-mix()` means a theme defines only core colors. Selection persists per
  user, defaulting to `nord`. See
  [THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md).
- The UI polls `GET /api/status` every 5 minutes and banners a newer server
  version.
- Library export: `GET /api/items/export?type=book&format=csv`.

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
                                     ├── Ranker (bonuses, preferences)
                                     └── [AI: LLM reasoning]     ← optional
                                                ↓
                                    Interface Layer (CLI/Web) → User
                                                ↓
                                    [Conversation System]  ← optional, AI-only
                                      Chat, memory, tools
```

## Configuration

`config/config.yaml` (git-ignored) holds the bootstrap: the `web` bind settings
and the `storage` paths, both read before the database opens.
`config/example.yaml` is that template and nothing more. Everything else resolves
through [global configuration precedence](#global-configuration-precedence), and
sources through
[source configuration precedence](#source-configuration-precedence).

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

Python 3.11+, SQLite, FastAPI, Click, Ollama (local, AMD-compatible) and
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
   `example.yaml` on first run), starting uvicorn through `CMD`.

`docker/Dockerfile.ollama` is a thin extension of `ollama/ollama` adding the
model-pull entrypoint.

`.github/workflows/docker.yml` builds all three on `linux/amd64` for a
`pull_request`, without pushing, and smoke-tests the default variant against
`/api/status`. On a `v*` tag it builds multi-arch, generates semver tags
(`X.Y.Z`, `X.Y`, `X`, `latest`), attaches provenance and SBOM attestations, and
pushes to GHCR. `.github/workflows/release.yml` creates that tag by running
python-semantic-release on every push to `main`, and uploads
`docker-compose.yml` as a release asset.

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
