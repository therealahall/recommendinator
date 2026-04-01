# Architecture Documentation

## Overview

Recommendinator ingests data from multiple sources and generates personalized recommendations using a smart scoring pipeline. **AI is entirely optional** — the system works fully without it. When enabled, a local LLM (via Ollama) provides semantic similarity, natural language explanations, and a conversational chat interface.

The architecture emphasizes modularity, testability, and extensibility.

## System Components

### 1. Data Ingestion Layer (`src/ingestion/`)

Responsible for parsing and normalizing data from various sources.

**Current Sources:**
- Goodreads CSV exports (books)
- Steam Web API (video games)
- GOG OAuth API (video games)
- Epic Games via Legendary (video games)
- Sonarr API (TV shows)
- Radarr API (movies)
- Generic CSV, JSON, Markdown (any content type)

**Design:**
- Plugin-based architecture (`SourcePlugin` ABC in `plugin_base.py`)
- Auto-discovered from `src/ingestion/sources/` via `PluginRegistry`
- **Named source instances**: Each entry under `inputs:` has a user-defined key name (e.g., `my_books`, `tv_shows`) with a `plugin:` field specifying the plugin type. Multiple instances of the same plugin are supported (e.g., two `json_import` sources with different files). The `resolve_inputs()` function in `src/web/sync_sources.py` is the central resolver that maps config entries to `(source_id, plugin, config)` tuples.
- `ContentItem.source` reflects the user-defined key name, not the plugin name, enabling per-instance tracking
- Each plugin handles config validation, fetching, and rating normalization
- Shared sync executor (`execute_multi_source_sync`) used by both CLI and web
- Progress callbacks for long-running operations
- Generic CSV/JSON importers support `ignored` field and `seasons_watched` as a list of specific season numbers

### 2. Storage Layer (`src/storage/`)

Manages persistent storage of processed data and embeddings.

**Components:**
- **SQLite Database**: Primary store for all structured data — content items, users, preferences, enrichment status, conversation history, core memories
- **ChromaDB** (optional): Vector embeddings for semantic search, only initialized when AI is enabled

**Schema:**
- `users` table with per-user settings (JSON)
- `content_items` table scoped by `user_id`
- Type-specific detail tables (`book_details`, `movie_details`, `tv_show_details`, `video_game_details`)
- `credentials` table for encrypted OAuth tokens and API keys (auto-migrated from config on startup)
- `enrichment_status` for tracking metadata enrichment
- `core_memories`, `conversation_messages`, `preference_profiles` for chat system

**Cross-Source Deduplication:**
Items imported from different sources (e.g., Steam and a personal blog) are automatically deduplicated by normalized title. When saving an item, the system first looks up an existing row by `(user_id, external_id, content_type)`. If found, it then checks for a *different* row with the same `(user_id, content_type, normalized_title)` and merges any such duplicate into the kept row. If no external_id match exists, it falls back to a direct normalized_title lookup to merge items from different sources. Merge rules: rating/review are filled from the duplicate only if the kept row is null; `date_completed` keeps the later date; genres/tags are merged additively; monotonic columns (seasons/episodes) keep the higher value; detail-table metadata is merged additively (existing keys preserved). Schema migrations re-normalize all titles and merge any duplicates exposed by the corrected normalization.

### 3. LLM Interaction Layer (`src/llm/`) — Optional

Handles communication with Ollama when AI features are enabled. **This entire layer is optional** — the system works fully without it.

**When Enabled, Provides:**
- Semantic embeddings for content similarity (ChromaDB)
- Natural language recommendation explanations
- Advanced preference rule interpretation

**Text Sanitization (`src/utils/text.py`):**
All user-provided text (memories, messages, metadata) is sanitized before interpolation into LLM prompts to prevent prompt injection:
- `sanitize_prompt_text(text)` — Strips newlines, control characters, and injection markers; caps at 100 chars
- `sanitize_prompt_text_long(text, max_length=200)` — Same sanitization with configurable cap, for conversation history where 100 chars is too restrictive
- `sanitize_prompt_text_with_truncation(text)` — Returns `(sanitized_text, was_truncated)` so callers can append ellipsis only on actual truncation
- `_sanitize_genre(text)` — Stricter allowlist for genre tags (no parentheses or colons); caps at 50 chars

**Feature Flags:**
- `features.ai_enabled` — Master toggle for all AI features
- `features.embeddings_enabled` — Vector similarity (requires ai_enabled)
- `features.llm_reasoning_enabled` — Natural language explanations (requires ai_enabled)

### 4. Recommendation Engine (`src/recommendations/`)

Core logic for generating recommendations with **cross-content-type support**.

**Architecture:** The engine uses a **unified scoring pipeline** that always runs. AI (embeddings, LLM reasoning) is an optional enhancement, not a requirement. Per-user preferences can override scorer weights at runtime.

```
RecommendationEngine
  |-- ScoringPipeline (always runs)
  |     |-- GenreMatchScorer      — genre preference scoring
  |     |-- CreatorMatchScorer    — author/director/developer matching
  |     |-- TagOverlapScorer      — threshold + cluster-based tag overlap
  |     |-- SeriesOrderScorer     — next-in-sequence boosting
  |     |-- RatingPatternScorer   — rating history in matching genres
  |     |-- ContentLengthScorer   — soft penalty for length preference mismatch
  |     |-- ContinuationScorer   — boost items the user is actively consuming (automatically excluded from pipeline when no actively-consumed items exist)
  |     |-- SeriesAffinityScorer — boost items in well-rated franchises (avg >= 4)
  |     |-- CustomPreferenceScorer — user natural language rules
  |     |-- [SemanticSimilarityScorer]  (when AI enabled)
  |
  |-- UserPreferenceConfig (optional per-user weight overrides, diversity_weight)
  |-- Ranker (adaptation bonus, series bonus, diversity bonus, preference adjustments)
  |-- [LLM reasoning post-processing]  (when AI enabled)
```

**Weight Resolution Order (last wins):**
1. Scorer class defaults (hardcoded: GenreMatch=2.0, etc.)
2. `config.yaml` scorer_weights section
3. Per-user DB settings (`users.settings` JSON → `"preference_config"` key)

**Process:**
1. Analyze user's consumed content (ratings, reviews) **across ALL content types**
2. Extract preferences and patterns (genres, themes, authors) from all consumed content
3. Load per-user preference config (if available), apply scorer weight overrides
4. Score all unconsumed candidates through the scoring pipeline
5. Optionally blend vector-similarity scores when AI is enabled
6. Apply series filtering with substitution (when `series_in_order` is enabled): candidates that fail series ordering rules are replaced with the earliest recommendable entry from the same series, using the substitute's own pipeline score. Duplicate substitutions per series are prevented. Also apply diversity bonus (genre-hopping) and ranking adjustments
7. Filter out items marked as `ignored`
8. Generate ranked recommendations with score breakdowns

**Cross-Content-Type Recommendations:**
- Preferences from all content types influence recommendations
- Metadata-based matching (genre/creator overlap) works without AI
- **Semantic genre clusters** (`genre_clusters.py`) group ~16 thematic clusters (e.g. science_fiction, war_military, fantasy) so items with related but different raw terms (e.g. book "space warfare" + TV "war") can connect
- **Compound genre splitting** (`genre_normalizer.py`) expands provider terms like "Sci-Fi & Fantasy" into constituent parts before normalization
- Cross-type reference items use cluster overlap instead of raw Jaccard to avoid broadly-matching items dominating all recommendations
- Optional vector embeddings for semantic similarity across content types

### 5. Metadata Enrichment (`src/enrichment/`)

Background system that fills gaps in content metadata from external APIs.

**Providers:**
- TMDB — movies and TV shows (includes collection/franchise extraction for series ordering)
- OpenLibrary — books (no API key required)
- RAWG — video games (includes franchise extraction via `game-series` endpoint)

**Franchise/Series Extraction:**
- TMDB: `belongs_to_collection` field provides franchise name and `series_position` via release-date ordering within the collection
- RAWG: `GET /games/{id}/game-series` returns related games; franchise name is derived from the longest common prefix of all game titles (e.g., "Dragon Age: Origins" + "Dragon Age II" -> "Dragon Age"), and position is determined by release-date ordering. Before computing the prefix, outlier titles are filtered out via majority-based first-word voting (e.g., "Lightning Returns: Final Fantasy XIII" is excluded when the other two titles start with "Final"). DLC suffixes like "+ Re Mind (DLC)" are stripped from titles before RAWG search to improve base-game matching. This handles games where title parsing fails (Dragon Age has no number, Kingdom Hearts uses decimals, Final Fantasy uses Roman numerals with slashes in HD remasters)
- Both store `franchise` and `series_position` in `extra_metadata` for consumption by the series ordering system

**Design:**
- `EnrichmentProvider` ABC with auto-discovery from `src/enrichment/providers/`
- Gap-filling merge strategy (never overwrites existing metadata)
- Token bucket rate limiter per provider
- Background worker with configurable batch size
- Optional auto-enrichment hook after sync

### 6. Conversation System (`src/conversation/`) — Optional

Conversational AI chat interface, requires AI to be enabled.

**Components:**
- `MemoryManager` — CRUD for core memories (preference signals)
- `ContextAssembler` — Builds dynamic context for LLM queries with safeguards:
  - **Single top-pick pipeline**: When the recommendation engine is available, only the single highest-ranked item is included in context (not a ranked list), keeping the LLM focused on hyping one pick
  - **Role-differentiated message sanitization**: User messages are sanitized via `sanitize_prompt_text()` / `sanitize_prompt_text_long()` to prevent prompt injection; assistant messages are only length-truncated (preserving LLM output formatting)
  - **Consumption status tagging**: Backlog items are tagged `[NOT YET CONSUMED]` in context to prevent the LLM from claiming the user enjoyed them
  - **Contributing items filter**: Only `COMPLETED`-status items appear in "Recently Completed" context, preventing backlog items from being misrepresented
  - **Qualitative fit labels**: Match scores (0–1) are converted to labels ("Excellent fit", "Strong fit", etc.) via `_score_to_qualitative()` instead of exposing raw percentages
- `ToolExecutor` — Tool-calling for data updates (mark completed, update rating, save memory)
- `IntentDetector` (`intent.py`) — Pre-LLM regex-based intent detection for tool actions (mark completed, rate, wishlist, preferences). When a high-confidence match is found, the tool action executes instantly without invoking the LLM
- `MemoryExtractor` — Extracts preferences from conversations
- `ProfileGenerator` — Computes genre affinities and preference profiles
- `ConversationEngine` — Orchestrator with streaming responses

**Compact Mode** (`conversation.context.compact_mode`):
When enabled (recommended for 3B models), the engine uses a condensed system prompt (~800 tokens), reduced context limits, compact item formatting, and pre-LLM intent detection. A separate `conversation_model` can be configured in `ollama` config to use a smaller model for chat while keeping the larger model for recommendations. See `docs/MODEL_RECOMMENDATIONS.md` for setup details.

### 7. Interface Layer

#### CLI (`src/cli/`)
- Click-based command structure
- Commands: `recommend`, `update`, `complete`, `preferences`, `enrichment`
- Supports batch operations and multiple output formats

#### Web (`src/web/` + `resources/`)
- **Backend**: FastAPI web server with REST API
- **Frontend**: Vue 3 SPA with Tailwind CSS v4, built with Vite
  - Source: `resources/js/` (Vue components, Pinia stores, composables, router) and `resources/css/` (CSS variables, Tailwind config)
  - Build output: `src/web/static/dist/` (Vite generates content-hashed asset bundles)
  - Dev server: Vite on `:5173` proxies `/api/*` and `/static/themes/*` to FastAPI on `:18473`
- Tabbed UI: Recommendations, Library, Chat, Data, Preferences (Chat hidden when AI is disabled)
- SSE streaming for chat responses and AI recommendation blurbs
- Library export: `GET /api/items/export?type=book&format=csv` (CSV or JSON download)
- **Themeable UI**: CSS custom properties system with folder-per-theme in `src/web/static/themes/`. Each theme provides a `theme.json` metadata file and a `colors.css` override. Tailwind `@theme` maps CSS vars to utility classes. Theme selection persisted per user via backend preferences (system default: `nord`). CSS uses `color-mix()` so themes only need to define core color variables. See `docs/THEME_DEVELOPMENT.md`.
- **Version display and update detection**: Version fetched from `GET /api/status`; UI polls every 5 minutes and displays a banner when a newer server version is detected
- **Asset cache busting**: Vite content-hashed filenames (e.g., `index-i5AIV_mm.js`)
- Internal network only (no external exposure)

## Data Flow

```
Data Sources (APIs, CSV, JSON, Markdown)
    ↓
Ingestion Layer (SourcePlugin → parse & normalize)
    ↓
Storage Layer (persist to SQLite; deduplicate cross-source items; optionally ChromaDB if AI enabled)
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

Configuration files in `config/`:
- `config.yaml`: Main configuration (git-ignored, contains secrets)
- `example.yaml`: Template with all options documented

Key sections: `features`, `ollama`, `storage`, `inputs`, `web`, `recommendations`, `conversation`, `enrichment`, `logging`.

The `inputs` section uses **named source instances**: each key is a user-defined name and must include a `plugin:` field to identify the plugin type. This allows multiple instances of the same plugin (e.g., separate JSON imports for books and movies). File-based plugins use a standardized `path` field. Example:

```yaml
inputs:
  my_books:
    plugin: json_import
    path: "inputs/books.json"
    content_type: "book"
    enabled: true
  my_movies:
    plugin: json_import
    path: "inputs/movies.json"
    content_type: "movie"
    enabled: true
```

## Extension Points

### Adding New Data Sources
1. Create plugin in `src/ingestion/sources/` implementing `SourcePlugin` ABC
2. Plugin is auto-discovered by `PluginRegistry`
3. Add tests with mocked APIs
4. See `docs/PLUGIN_DEVELOPMENT.md` for details

### Adding New Enrichment Providers
1. Create provider in `src/enrichment/providers/` implementing `EnrichmentProvider` ABC
2. Provider is auto-discovered by `EnrichmentRegistry`
3. Add rate limiting configuration
4. Add tests

### Adding New Content Types
1. Extend `ContentType` enum
2. Add type-specific detail table in schema
3. Add type-specific recommendation logic
4. Update data models

## Technology Stack

- **Python**: 3.11+
- **LLM**: Ollama (local, AMD-compatible)
- **Vector DB**: ChromaDB (optional, AI-only)
- **SQL Database**: SQLite
- **Web Framework**: FastAPI
- **CLI Framework**: Click
- **Testing**: pytest
- **Quality**: Black, MyPy (strict), Ruff

### Development Tooling (Claude Code)

- **Pyright LSP Plugin** — Real-time static type analysis via Language Server Protocol, catches type errors and missing annotations
- **Frontend Design Plugin** — Generates production-grade UI components for the web interface
- **Security-Review Agent** — Pre-commit security audit agent that checks for credential exposure, injection vulnerabilities, CORS misconfigurations, and project-specific security rules. See `docs/SECURITY.md` for details.
- **Code-Review Agent** — Pre-commit code quality agent that performs line-by-line review for dead code, code smells, DRY violations, naming, type safety, over/under-engineering, and project standards compliance.
- **Test-Review Agent** — Pre-commit test coverage and quality audit agent that verifies test completeness, mock hygiene, regression test format, and edge case coverage.
- **Document-Review Agent** — Documentation accuracy and completeness audit agent that checks for staleness, cross-document consistency, and missing documentation.
- **Accessibility-Review Agent** — Pre-commit accessibility audit agent that verifies WCAG 2.1 Level AA compliance for frontend code (semantic HTML, ARIA attributes, keyboard navigation, focus management, color contrast). Self-gates on frontend file presence — immediately approves backend-only changes.
- **Commit-Hygiene Agent** — Atomic commit structure and conventional format enforcement agent that plans commit splits and verifies message quality.

Plugin configuration: `.claude/settings.json` | Agent definitions: `.claude/agents/`

## Security & Privacy

- All processing happens locally
- External API calls limited to: data source APIs (Steam, GOG, Epic, Sonarr, Radarr), enrichment APIs (TMDB, OpenLibrary, RAWG), and Ollama (local)
- Web interface accessible on internal network only
- API keys stored in git-ignored `config/config.yaml`
- See `docs/SECURITY.md` for details

## Future Enhancements

- Discovery mode (surface things you didn't know about)
- Interactive refinement ("I'm burnt out on sci-fi")
- Scheduled sync (cron-style)
