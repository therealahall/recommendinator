# Architecture Documentation

## Overview

The Personal Recommendations system ingests data from multiple sources and generates personalized recommendations using a smart scoring pipeline. **AI is entirely optional** — the system works fully without it. When enabled, a local LLM (via Ollama) provides semantic similarity, natural language explanations, and a conversational chat interface.

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
- `enrichment_status` for tracking metadata enrichment
- `core_memories`, `conversation_messages`, `preference_profiles` for chat system

### 3. LLM Interaction Layer (`src/llm/`) — Optional

Handles communication with Ollama when AI features are enabled. **This entire layer is optional** — the system works fully without it.

**When Enabled, Provides:**
- Semantic embeddings for content similarity (ChromaDB)
- Natural language recommendation explanations
- Advanced preference rule interpretation

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
  |     |-- TagOverlapScorer      — Jaccard genre/tag overlap
  |     |-- SeriesOrderScorer     — next-in-sequence boosting
  |     |-- RatingPatternScorer   — rating history in matching genres
  |     |-- ContentLengthScorer   — soft penalty for length preference mismatch
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
6. Apply series filtering (gap-finding for non-sequential season watching), diversity bonus (genre-hopping), and ranking adjustments
7. Filter out items marked as `ignored`
8. Generate ranked recommendations with score breakdowns

**Cross-Content-Type Recommendations:**
- Preferences from all content types influence recommendations
- Metadata-based matching (genre/creator overlap) works without AI
- Optional vector embeddings for semantic similarity across content types

### 5. Metadata Enrichment (`src/enrichment/`)

Background system that fills gaps in content metadata from external APIs.

**Providers:**
- TMDB — movies and TV shows
- OpenLibrary — books (no API key required)
- RAWG — video games

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
- `ContextAssembler` — RAG retrieval for relevant items
- `ToolExecutor` — Tool-calling for data updates (mark completed, update rating, save memory)
- `MemoryExtractor` — Extracts preferences from conversations
- `ProfileGenerator` — Computes genre affinities and preference profiles
- `ConversationEngine` — Orchestrator with streaming responses

### 7. Interface Layer

#### CLI (`src/cli/`)
- Click-based command structure
- Commands: `recommend`, `update`, `complete`, `preferences`, `enrichment`
- Supports batch operations and multiple output formats

#### Web (`src/web/`)
- FastAPI web server with REST API
- Tabbed web UI: Recommendations, Chat, Library, Preferences, Sync
- Chat tab hidden when AI is disabled
- SSE streaming for chat responses
- Library export: `GET /api/items/export?type=book&format=csv` (CSV or JSON download)
- Internal network only (no external exposure)

## Data Flow

```
Data Sources (APIs, CSV, JSON, Markdown)
    ↓
Ingestion Layer (SourcePlugin → parse & normalize)
    ↓
Storage Layer (persist to SQLite; optionally ChromaDB if AI enabled)
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
