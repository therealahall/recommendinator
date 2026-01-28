# Development Log

This document tracks the development progress, decisions, and steps taken during the project build.

> **V1 Roadmap:** For the forward-looking implementation plan, see [docs/V1_ROADMAP.md](docs/V1_ROADMAP.md).

## Table of Contents
- [Initial Setup](#initial-setup)
- [Phase 1: Project Foundation](#phase-1-project-foundation)
- [Phase 2: Data Ingestion](#phase-2-data-ingestion)
- [Phase 3: Storage Layer](#phase-3-storage-layer)
- [Phase 4: LLM Integration](#phase-4-llm-integration)
- [Phase 5: Recommendation Engine](#phase-5-recommendation-engine)
- [Phase 6: CLI Interface](#phase-6-cli-interface)
- [Phase 7: Web Interface](#phase-7-web-interface)
- [Decisions & Notes](#decisions--notes)
- [V1 Architecture Pivot](#v1-architecture-pivot)

---

## Initial Setup

**Date:** 2026-01-18  
**Status:** ✅ Completed

### Steps Taken

1. **Project Requirements Gathering**
   - Clarified data sources (Goodreads CSV as first source)
   - Identified content types: Books, Movies, TV Shows, Video Games
   - Confirmed rating scale: 1-5 stars
   - Determined interface needs: CLI + Web (internal network)
   - Selected Ollama for local LLM (AMD architecture support)
   - Decided on Python 3.11+ (user has 3.14.2)

2. **Technology Stack Decisions**
   - **LLM**: Ollama (local, supports AMD)
   - **Model**: mistral:7b (default, configurable)
   - **Vector DB**: ChromaDB (lightweight, local-first)
   - **SQL Database**: SQLite (simple, no external dependencies)
   - **Web Framework**: FastAPI (modern, async, good docs)
   - **CLI Framework**: Click
   - **Testing**: pytest
   - **Linting**: Black, MyPy, Ruff

3. **Storage Strategy**
   - Vector database for embeddings (semantic search)
   - SQLite for structured data (ratings, metadata, status)
   - File-based cache for raw data and processing state

---

## Phase 1: Project Foundation

**Date:** 2026-01-18  
**Status:** ✅ Completed

### Steps Taken

1. **Created Project Documentation**
   - `README.md` - Project overview, features, usage
   - `CONTRIBUTING.md` - Development guidelines, commit conventions
   - `ARCHITECTURE.md` - Technical architecture and design decisions
   - `QUICKSTART.md` - Getting started guide

2. **Set Up Project Structure**
   ```
   personal-recommendations/
   ├── src/
   │   ├── cli/              # CLI interface (placeholder)
   │   ├── web/               # Web interface (placeholder)
   │   ├── ingestion/         # Data ingestion
   │   │   └── sources/       # Source parsers
   │   ├── llm/               # LLM interaction (placeholder)
   │   ├── storage/           # Storage layer (placeholder)
   │   ├── recommendations/  # Recommendation engine (placeholder)
   │   ├── models/            # Data models
   │   └── utils/             # Utilities
   ├── tests/                 # Test suite
   ├── inputs/                # Input data files
   ├── config/                # Configuration
   └── docs/                  # Documentation
   ```

3. **Configured Development Tools**
   - `pyproject.toml` - Black, MyPy, Ruff, pytest configuration
   - `requirements.txt` - Production dependencies
   - `requirements-dev.txt` - Development dependencies
   - `.gitignore` - Git ignore patterns
   - `Makefile` - Common development commands

4. **Created Data Models**
   - `ContentType` enum (BOOK, MOVIE, TV_SHOW, VIDEO_GAME)
   - `ConsumptionStatus` enum (UNREAD, CURRENTLY_CONSUMING, COMPLETED)
   - `ContentItem` Pydantic model with validation
   - Fixed Pydantic v2 deprecation warning (ConfigDict instead of Config class)

### Key Decisions
- Used Pydantic v2 with `ConfigDict` for modern Python practices
- Structured project with clear separation of concerns
- Set up comprehensive linting and type checking from the start

---

## Phase 2: Data Ingestion

**Date:** 2026-01-18  
**Status:** ✅ Completed

### Steps Taken

1. **Analyzed Goodreads CSV Structure**
   - Examined `inputs/goodreads_library_export.csv`
   - Identified key columns:
     - Title, Author
     - My Rating (0-5, 0 = unread)
     - Exclusive Shelf (to-read, currently-reading, read)
     - Date Read
     - My Review
     - Additional metadata (pages, ISBN, etc.)

2. **Implemented Goodreads Parser**
   - Created `src/ingestion/sources/goodreads.py`
   - `parse_goodreads_csv()` function that yields `ContentItem` objects
   - Handles:
     - Rating parsing (0 = None/unread)
     - Shelf status mapping to `ConsumptionStatus`
     - Date parsing (YYYY/MM/DD format)
     - Review extraction
     - Metadata preservation
     - Empty title skipping

3. **Created Test Suite**
   - `tests/test_goodreads_parser.py`
   - Tests for:
     - Basic parsing (completed vs unread)
     - Currently-reading status
     - Empty title handling
   - All tests passing ✅

### Key Decisions
- Parser yields items (generator) for memory efficiency
- Empty titles are skipped (data quality)
- Rating of 0 is treated as None (unrated/unread)
- Metadata preserved in dict for future use

### Files Created
- `src/ingestion/sources/goodreads.py`
- `tests/test_goodreads_parser.py`

---

## Phase 3: Storage Layer

**Date:** 2026-01-18  
**Status:** ✅ Completed

### Steps Taken

1. **SQLite Database Setup**
   - Created database schema in `src/storage/schema.py`
   - Single `content_items` table with all necessary fields
   - Indexes on common query fields (content_type, status, rating, date_completed)
   - Schema versioning system for future migrations
   - CRUD operations implemented in `src/storage/sqlite_db.py`

2. **ChromaDB Vector Database Setup**
   - Created `src/storage/vector_db.py` for vector database management
   - ChromaDB integration with persistent storage
   - Embedding storage and retrieval
   - Similarity search with filtering support
   - Metadata storage for content items

3. **Storage Manager**
   - Created unified `StorageManager` in `src/storage/manager.py`
   - Combines SQLite and ChromaDB operations
   - Save content items with optional embeddings
   - Search similar content using vector similarity
   - Delete operations clean up both databases

4. **Test Suite**
   - Comprehensive tests for SQLite database (`tests/test_sqlite_db.py`)
   - Tests for vector database (`tests/test_vector_db.py`)
   - Tests for storage manager (`tests/test_storage_manager.py`)
   - **All 24 tests passing** ✅ (SQLite, ChromaDB, and Storage Manager)

### Key Decisions
- Single table design for content_items (simpler than normalized schema)
- JSON storage for metadata (flexible, easy to extend)
- Handle Pydantic enum-to-string conversion (use_enum_values=True)
- ChromaDB for vector operations (lightweight, local-first)
- Unified manager interface (simplifies usage)
- Use Python 3.11 for ChromaDB compatibility (Python 3.14 not fully supported yet)
- Handle numpy array conversion from ChromaDB (embeddings returned as numpy arrays)
- Use approximate equality in tests for floating-point comparisons

### Files Created
- `src/storage/schema.py` - Database schema and migrations
- `src/storage/sqlite_db.py` - SQLite database manager
- `src/storage/vector_db.py` - ChromaDB vector database manager
- `src/storage/manager.py` - Unified storage manager
- `tests/test_sqlite_db.py` - SQLite tests
- `tests/test_vector_db.py` - Vector DB tests
- `tests/test_storage_manager.py` - Storage manager tests

### Design Considerations
- Use SQLite for structured queries (ratings, status, metadata) ✅
- Use ChromaDB for semantic search (similarity matching) ✅
- Consider caching strategy for performance (future optimization)
- Handle concurrent access if needed (SQLite handles this)

---

## Phase 4: LLM Integration

**Date:** January 18, 2025  
**Status:** ✅ Completed

### Implementation Summary

Successfully implemented full LLM integration layer with Ollama support for AMD architecture.

### Completed Steps

1. **Ollama Client Wrapper** ✅
   - Connection handling with configurable base URL
   - Model selection (separate models for embeddings vs recommendations)
   - Error handling and retries
   - Timeout management (default 300s)
   - Model availability checking
   - List available models functionality

2. **Embedding Generation** ✅
   - Generate embeddings for content items (title, author, review, metadata)
   - Generate embeddings for review text
   - Batch processing support
   - Integration with EmbeddingGenerator class

3. **Prompt Engineering** ✅
   - System prompts for recommendations (content-type specific)
   - Context building from user consumption history
   - Rating analysis (focus on 4-5 star items)
   - Template system in `prompts.py`
   - Content description builder for embeddings

4. **Response Parsing** ✅
   - Parse LLM recommendations from numbered lists
   - Extract title, author, and reasoning
   - Match recommendations to unconsumed items
   - Fallback parsing for various response formats
   - Error recovery

### Files Created
- `src/llm/client.py` - Ollama client wrapper
- `src/llm/prompts.py` - Prompt templates and builders
- `src/llm/embeddings.py` - Embedding generation
- `src/llm/recommendations.py` - Recommendation generation
- `tests/test_ollama_client.py` - Client tests (9 tests)
- `tests/test_embeddings.py` - Embedding tests (4 tests)
- `tests/test_recommendations.py` - Recommendation tests (3 tests)
- `docs/MODEL_RECOMMENDATIONS.md` - Model selection guide

### Design Decisions

1. **Two-Model Strategy** ✅
   - **Embedding Model**: `nomic-embed-text` (specialized, ~274 MB)
   - **Recommendation Model**: `mistral:7b` or `deepseek-r1:latest` (general purpose)
   - Rationale: Embedding models are optimized for vector generation, text models for reasoning

2. **Prompt Templates in Code** ✅
   - Templates defined in `prompts.py` module
   - Easy to modify and version control
   - Content-type specific prompts
   - Context-aware (uses high-rated items)

3. **Error Handling** ✅
   - RuntimeError for LLM failures
   - Graceful degradation (empty recommendations if no items)
   - Logging for debugging
   - Model availability checks

4. **AMD Compatibility** ✅
   - All recommended models work on AMD processors
   - Supports both GPU (ROCm) and CPU modes
   - Documented in MODEL_RECOMMENDATIONS.md

### Testing
- **16 LLM integration tests** (all passing)
- Mock-based testing for Ollama client
- Tests for embedding generation, recommendation parsing
- Error handling tests

### Next Steps
- Integration with storage layer (save embeddings to ChromaDB)
- Integration with recommendation engine (use embeddings for similarity)
- Real-world testing with actual Ollama models

---

## Phase 5: Recommendation Engine

**Date:** January 18, 2025  
**Status:** ✅ Completed

### Implementation Summary

Successfully implemented comprehensive recommendation engine combining vector similarity, preference analysis, and ranking algorithms.

### Completed Steps

1. **Preference Analysis** ✅
   - Analyze consumed content (ratings, reviews)
   - Extract patterns (genres, themes, authors)
   - Identify high-rated preferences (default: 4+ stars)
   - Build user profile with weighted scores
   - Normalize preference scores (0.0-1.0)

2. **Similarity Matching** ✅
   - Use vector embeddings to find similar content
   - Filter by content type
   - Exclude already consumed items
   - Average multiple reference embeddings for query
   - Generate embeddings on-the-fly if missing

3. **Ranking Algorithm** ✅
   - Combine similarity scores with preferences
   - Configurable weights (similarity, preference, diversity)
   - Default: 60% similarity, 30% preference, 10% diversity
   - Generate ranked list with metadata
   - Include reasoning for each recommendation

4. **Recommendation Generation** ✅
   - Generate N recommendations
   - Provide explanations based on similarity and preferences
   - Support filtering (type, count)
   - Optional LLM integration for enhanced reasoning
   - Handle cold start scenario (no consumed items)

### Files Created
- `src/recommendations/preferences.py` - Preference analysis
- `src/recommendations/similarity.py` - Vector similarity matching
- `src/recommendations/ranking.py` - Ranking algorithm
- `src/recommendations/engine.py` - Main recommendation engine
- `tests/test_preferences.py` - Preference tests (5 tests)
- `tests/test_ranking.py` - Ranking tests (3 tests)
- `tests/test_recommendation_engine.py` - Engine tests (3 tests)

### Design Decisions

1. **Preference Weighting** ✅
   - **Decision**: Weight preferences by rating (4-star = 0.5, 5-star = 1.0)
   - **Rationale**: Higher-rated items indicate stronger preferences
   - **Normalization**: Scores normalized to 0.0-1.0 range

2. **Similarity Matching** ✅
   - **Decision**: Average multiple reference embeddings for query
   - **Rationale**: Captures broader preferences from multiple favorites
   - **On-demand**: Generate embeddings if missing, cache for future use

3. **Ranking Algorithm** ✅
   - **Decision**: Weighted combination (60% similarity, 30% preference, 10% diversity)
   - **Rationale**: Balance semantic similarity with explicit preferences
   - **Configurable**: Weights can be adjusted per use case

4. **Cold Start Handling** ✅
   - **Decision**: Return empty recommendations for cold start
   - **Rationale**: Need consumption history for meaningful recommendations
   - **Future**: Could add default/popular items strategy

5. **LLM Integration** ✅
   - **Decision**: Optional LLM enhancement for reasoning
   - **Rationale**: Vector similarity + preferences provide base, LLM adds context
   - **Default**: Off (can be enabled with `use_llm=True`)

### Testing
- **11 new recommendation engine tests** (all passing)
- Total: **51 tests passing**
- Mock-based testing for storage and LLM components
- Tests for preferences, ranking, and full engine workflow

### Integration Points
- ✅ Storage Manager (SQLite + ChromaDB)
- ✅ Embedding Generator (LLM layer)
- ✅ Optional LLM Recommendation Generator
- ✅ Content models (ContentItem, ContentType)

### Next Steps
- Integration with CLI interface (Phase 6)
- Integration with web interface (Phase 7)
- Real-world testing with actual data
- Performance optimization (caching, batch processing)

---

## Phase 6: CLI Interface

**Date:** January 18, 2025  
**Status:** ✅ Completed

### Implementation Summary

Successfully implemented comprehensive CLI interface using Click framework with full integration to recommendation engine and storage layer.

### Completed Steps

1. **CLI Framework Setup** ✅
   - Click-based command structure
   - Command groups (recommend, update, complete)
   - Configuration file loading
   - Component initialization
   - Help text and documentation

2. **Recommendation Commands** ✅
   - `recommend --type book --count 5`
   - Support for all content types (book, movie, tv_show, video_game)
   - Output formatting (table, JSON)
   - Optional LLM enhancement (`--use-llm` flag)
   - Error handling and user-friendly messages

3. **Update Commands** ✅
   - `update --source goodreads` (re-ingest files)
   - `update --source all` (reprocess everything)
   - Progress indicators (every 10 items)
   - Automatic embedding generation
   - Error handling for missing files

4. **Completion Commands** ✅
   - `complete --type book --title "Title" --rating 4`
   - Support for all content types
   - Author field for books
   - Optional rating and review
   - Validation (rating 1-5)
   - Automatic embedding generation

### Files Created
- `src/cli/__init__.py` - CLI module
- `src/cli/config.py` - Configuration loading and component creation
- `src/cli/main.py` - Main CLI entry point
- `src/cli/commands.py` - Command implementations
- `tests/test_cli.py` - CLI tests (8 tests)

### Design Decisions

1. **Click Framework** ✅
   - **Decision**: Use Click for CLI framework
   - **Rationale**: Well-established, feature-rich, good help generation
   - **Benefits**: Automatic help text, argument parsing, command groups

2. **Configuration Loading** ✅
   - **Decision**: Load from YAML config file
   - **Rationale**: Centralized configuration, easy to modify
   - **Fallback**: Uses example.yaml if config.yaml not found

3. **Output Formats** ✅
   - **Decision**: Support table (default) and JSON formats
   - **Rationale**: Table for human readability, JSON for scripting
   - **Implementation**: Uses `tabulate` for table formatting

4. **Component Initialization** ✅
   - **Decision**: Initialize all components at CLI startup
   - **Rationale**: Single initialization, shared across commands
   - **Error Handling**: Graceful failure with helpful error messages

5. **Command Structure** ✅
   - **Decision**: Separate commands for different operations
   - **Rationale**: Clear separation of concerns, easy to extend
   - **Commands**: recommend, update, complete

### Testing
- **8 new CLI tests** (all passing)
- Total: **56 tests passing**
- Mock-based testing for all components
- Tests for help, commands, output formats, validation

### Integration Points
- ✅ Recommendation Engine
- ✅ Storage Manager
- ✅ LLM Components (Ollama client, embeddings, recommendations)
- ✅ Data Ingestion (Goodreads parser)
- ✅ Configuration system

### Usage Examples

```bash
# Get book recommendations
python -m src.cli.main recommend --type book --count 5

# Get recommendations in JSON format
python -m src.cli.main recommend --type book --count 5 --format json

# Update data from Goodreads
python -m src.cli.main update --source goodreads

# Mark a book as completed
python -m src.cli.main complete --type book --title "Book Title" --author "Author" --rating 4
```

### Next Steps
- Integration with web interface (Phase 7)
- Real-world testing with actual data
- Performance optimization
- Additional output formats (CSV, etc.)

---

## Phase 7: Web Interface

**Date:** January 18, 2025  
**Status:** ✅ Completed

### Implementation Summary

Successfully implemented comprehensive web interface using FastAPI with REST API endpoints and a modern, mobile-friendly web UI.

### Completed Steps

1. **FastAPI Server Setup** ✅
   - FastAPI application structure
   - CORS configuration (configurable, defaults to all origins for internal network)
   - Health check endpoint (`/api/status`)
   - Static file serving for web UI
   - Component initialization and state management

2. **REST API Endpoints** ✅
   - `GET /api/recommendations?type=book&count=5` - Get recommendations
   - `POST /api/complete` - Mark content as completed
   - `POST /api/update` - Trigger data update from files
   - `GET /api/status` - System status and component health
   - Pydantic models for request/response validation
   - Comprehensive error handling

3. **Web UI** ✅
   - Simple HTML/CSS/JS interface (no framework dependencies)
   - Modern, responsive design with gradient background
   - Recommendation display with cards
   - Content type selection
   - Count and LLM options
   - Mobile-friendly design (responsive layout)
   - Real-time status checking
   - Error handling and user feedback

4. **Security** ✅
   - CORS configuration (configurable via config file)
   - Internal network access (host: 0.0.0.0 for local network)
   - Input validation via Pydantic models
   - Error handling without exposing internals

### Files Created
- `src/web/__init__.py` - Web module
- `src/web/app.py` - FastAPI application setup
- `src/web/api.py` - REST API endpoints
- `src/web/state.py` - Application state management
- `src/web/main.py` - Web server entry point
- `src/web/templates/index.html` - Web UI
- `tests/test_web_api.py` - API tests (7 tests)

### Design Decisions

1. **FastAPI Framework** ✅
   - **Decision**: Use FastAPI for web framework
   - **Rationale**: Modern, fast, automatic API documentation, type safety
   - **Benefits**: Built-in validation, async support, OpenAPI docs at `/docs`

2. **Simple Web UI** ✅
   - **Decision**: Vanilla HTML/CSS/JS (no framework)
   - **Rationale**: Lightweight, fast loading, easy to customize
   - **Benefits**: No build step, works offline, mobile-friendly

3. **State Management** ✅
   - **Decision**: Global app state module
   - **Rationale**: Avoid circular imports, easy access from endpoints
   - **Implementation**: Separate `state.py` module

4. **CORS Configuration** ✅
   - **Decision**: Configurable CORS (defaults to all origins)
   - **Rationale**: Internal network use, can be restricted via config
   - **Security**: User should configure for their network

5. **API Design** ✅
   - **Decision**: RESTful API with Pydantic models
   - **Rationale**: Type safety, automatic validation, clear contracts
   - **Documentation**: Automatic OpenAPI docs at `/docs`

### Testing
- **7 new web API tests** (all passing)
- Total: **63 tests passing**
- TestClient-based testing for FastAPI
- Tests for all endpoints, validation, error handling

### Integration Points
- ✅ CLI Components (config, storage, engine)
- ✅ Recommendation Engine
- ✅ Storage Manager
- ✅ LLM Components
- ✅ Data Ingestion

### Usage

```bash
# Start web server
python -m src.web.main

# Or with custom config
python -m src.web.main --config config/config.yaml

# Access web UI
# http://localhost:8000/

# Access API documentation
# http://localhost:8000/docs

# API endpoints
# GET http://localhost:8000/api/recommendations?type=book&count=5
# POST http://localhost:8000/api/complete
# POST http://localhost:8000/api/update
# GET http://localhost:8000/api/status
```

### Next Steps
- Real-world testing with actual data
- Performance optimization
- Additional features (filtering, sorting, etc.)
- Optional authentication if needed

---

## Decisions & Notes

### Architecture Decisions

1. **Storage Strategy**
   - **Decision**: Hybrid approach (SQLite + ChromaDB)
   - **Rationale**: SQLite for structured queries, ChromaDB for semantic search
   - **Alternative Considered**: Single database solution
   - **Trade-off**: More complexity but better performance for different use cases

2. **Data Model Design**
   - **Decision**: Pydantic models with enums
   - **Rationale**: Type safety, validation, modern Python practices
   - **Alternative Considered**: Dataclasses or plain dicts
   - **Trade-off**: Slight overhead but better developer experience

3. **Parser Architecture**
   - **Decision**: Generator-based parsers (yield items)
   - **Rationale**: Memory efficient for large files
   - **Alternative Considered**: Return lists
   - **Trade-off**: Slightly more complex but scales better

4. **Testing Strategy**
   - **Decision**: pytest with high coverage target (80%+)
   - **Rationale**: Catch bugs early, enable refactoring
   - **Alternative Considered**: Minimal testing
   - **Trade-off**: More time upfront but safer long-term

### Open Questions

1. **Model Selection** ✅ RESOLVED
   - **Decision**: Use two models - `nomic-embed-text` for embeddings, `mistral:7b` or `deepseek-r1:latest` for recommendations
   - **Rationale**: Embedding models are optimized for vector generation, text models for reasoning
   - **AMD Compatible**: All recommended models work on AMD processors
   - See `docs/MODEL_RECOMMENDATIONS.md` for details

2. **Update Strategy**
   - Full reprocessing vs incremental updates?
   - How to detect file changes?
   - When to trigger automatic updates?

3. **Content-Type-Specific Logic**
   - User mentioned "strong recommendations" - need to clarify
   - How to handle different rating patterns per type?
   - Genre/tag extraction strategy?

### Lessons Learned

- Pydantic v2 migration: Use `ConfigDict` instead of `Config` class
- Test early: Having tests from the start catches issues quickly
- Documentation matters: Clear docs help with onboarding and decisions

### Future Enhancements

- Multi-user support (if needed)
- Advanced filtering (genre, length, year, etc.)
- Recommendation explanations with reasoning
- Export/import functionality
- Integration with external APIs (optional, privacy-preserving)

---

## Progress Summary

| Phase | Status | Completion |
|-------|--------|------------|
| Initial Setup | ✅ Complete | 100% |
| Phase 1: Foundation | ✅ Complete | 100% |
| Phase 2: Data Ingestion | ✅ Complete | 100% |
| Phase 3: Storage Layer | ✅ Complete | 100% |
| Phase 4: LLM Integration | ✅ Complete | 100% |
| Phase 5: Recommendation Engine | ✅ Complete | 100% |
| Phase 6: CLI Interface | ✅ Complete | 100% |
| Phase 7: Web Interface | ✅ Complete | 100% |

**Overall Progress:** ~100% (All phases complete! 🎉)

---

## How to Use This Document

- **Update after each phase**: Document what was done, decisions made, and lessons learned
- **Reference for decisions**: When making similar choices, check what was decided before
- **Progress tracking**: See what's done and what's next
- **Onboarding**: New contributors can understand the project history

---

## V1 Architecture Pivot

**Date:** 2026-01-25
**Status:** Planning Complete, Implementation Starting

### Background

The initial prototype (Phases 1-7 above) successfully demonstrated the core concept. However, after comprehensive review, several architectural changes are needed for v1:

1. **AI should be optional** - The system must work without LLM/embeddings for users who prefer not to use AI
2. **Multi-user support** - Schema should support multiple users from day 1
3. **Plugin architecture** - Formalize the data source interface for community contributions
4. **Scoring pipeline** - Replace monolithic recommendation engine with composable scorers

### Key Decisions

| Decision | Rationale |
|----------|-----------|
| AI as enhancement, not requirement | Broader user appeal, better engineering discipline |
| Hybrid scoring (content-based + rule-based) | Works without AI, AI becomes just another scorer |
| User table from day 1 | Avoid painful migration later |
| Plugin interface for sources | Enable community contributions |
| Preferences in database | Per-user, UI-editable, portable |

### What's Changing

- **Schema v3**: Add `users` table, add `user_id` to content_items
- **StorageManager**: ChromaDB becomes optional (lazy init when AI enabled)
- **Recommendation Engine**: Refactor to scoring pipeline architecture
- **Ingestion**: Formalize plugin interface (SourcePlugin ABC)
- **Configuration**: Add feature flags for AI on/off

### Implementation Plan

See [docs/V1_ROADMAP.md](docs/V1_ROADMAP.md) for the detailed phase-by-phase implementation plan.

---

## V1 Phase 3: Non-AI Recommendation Engine

**Date:** 2026-01-27
**Status:** ✅ Completed

### Background

The existing `RecommendationEngine` required `EmbeddingGenerator` (AI/Ollama) to function. When AI is off (the default), the system could not produce meaningful recommendations. Phase 3 builds the non-AI scoring foundation that **always runs**, with AI becoming an additional scorer on top (Phase 4).

### Design Decision: Unified Flow

The scoring pipeline always runs. AI is **not** a separate branch — it's an additional scorer added to the pipeline when enabled:

```
RecommendationEngine
  |-- ScoringPipeline (always)
  |     |-- GenreMatchScorer (weight 2.0)
  |     |-- CreatorMatchScorer (weight 1.5)
  |     |-- TagOverlapScorer (weight 1.0)
  |     |-- SeriesOrderScorer (weight 1.5)
  |     |-- RatingPatternScorer (weight 1.0)
  |     |-- [SemanticSimilarityScorer]  <-- Phase 4: added when AI enabled
  |
  |-- Ranker (adaptation bonus, series bonus, preference adjustments)
  |-- [LLM reasoning post-processing]  <-- Phase 4: added when AI enabled
```

### Completed Steps

1. **Created Scorers Module** (`src/recommendations/scorers.py`) ✅
   - `ScoringContext` dataclass: pre-computes consumed genres, creators, ratings by genre
   - `Scorer` ABC with `weight` and `score()` method
   - `GenreMatchScorer`: maps preference genre score [-1,1] into [0,1]
   - `CreatorMatchScorer`: unified author/director/developer matching via preferences and consumed set
   - `TagOverlapScorer`: Jaccard overlap of candidate genres vs consumed genres
   - `SeriesOrderScorer`: 1.0 next-in-sequence, 0.8 first-unstarted, 0.3 too-far-ahead, 0.5 non-series
   - `RatingPatternScorer`: average rating in matching genres mapped to [0,1]
   - Helper functions: `_extract_genres()`, `_extract_creator()`

2. **Created Scoring Pipeline** (`src/recommendations/scoring_pipeline.py`) ✅
   - Weight-normalized aggregate scores in [0,1]
   - Returns candidates sorted descending by score
   - Handles edge cases: empty candidates, zero-weight scorers

3. **Refactored Recommendation Engine** (`src/recommendations/engine.py`) ✅
   - `embedding_generator` is now `EmbeddingGenerator | None = None`
   - `SimilarityMatcher` only created when `embedding_generator` is provided
   - `ScoringPipeline` always runs on all unconsumed candidates
   - When AI available: pipeline scores blended with similarity scores
   - `_find_contributing_reference_items()` uses metadata-based matching (genre/creator overlap) — no embeddings required
   - LLM reasoning block preserved but guarded by `use_llm and self.llm_generator`

4. **Updated Exports and Config** ✅
   - `src/recommendations/__init__.py` exports all new symbols
   - `config/example.yaml` adds `recommendations.scorer_weights` section

5. **Comprehensive Test Suite** ✅
   - `tests/test_scorers.py`: 28 tests covering helpers, context, and all 5 scorers
   - `tests/test_scoring_pipeline.py`: 5 tests (sorting, empty, normalization, clamping, zero-weight)
   - `tests/test_recommendation_engine.py`: 6 new non-AI tests + 4 existing tests still passing

### Files Created/Modified

| File | Action |
|------|--------|
| `src/recommendations/scorers.py` | **Created** — ScoringContext, Scorer ABC, 5 scorers |
| `src/recommendations/scoring_pipeline.py` | **Created** — Weight-normalized pipeline |
| `src/recommendations/engine.py` | **Modified** — AI optional, pipeline always runs |
| `src/recommendations/__init__.py` | **Modified** — New exports |
| `config/example.yaml` | **Modified** — scorer_weights config |
| `tests/test_scorers.py` | **Created** — 28 tests |
| `tests/test_scoring_pipeline.py` | **Created** — 5 tests |
| `tests/test_recommendation_engine.py` | **Modified** — 6 new non-AI tests |

### Key Design Decisions

1. **Scorers return [0, 1]**: All scorers normalize their output to a unit interval. The pipeline weight-normalizes the aggregate.
2. **ScoringContext pre-computes**: Lookup structures (consumed genres, creators, ratings by genre) are built once and shared across all scorers.
3. **Metadata-based contributing items**: `_find_contributing_reference_items` uses genre/creator overlap instead of embeddings, so cross-content reasoning works without AI.
4. **Blending strategy**: When AI is available, pipeline scores are averaged with similarity scores. This is a simple approach that Phase 4 can refine by adding a `SemanticSimilarityScorer` directly to the pipeline.

### Testing

- **476 total tests passing** (39 new)
- All quality checks clean: ruff, black, mypy (no new errors)

---

## V1 Phase 4: AI Enhancement Layer

**Date:** 2026-01-27
**Status:** ✅ Completed

### Background

Phase 3 left AI similarity scores bolted on *after* the scoring pipeline as a separate blending step (naive 50/50 average of pipeline scores and similarity scores). Phase 4 moves AI into the pipeline as a proper `SemanticSimilarityScorer`, making it participate in weighted aggregation alongside all other scorers.

### Design Decision: Pre-computed Similarity Scores

The scorer pattern calls `score(candidate, context)` per-candidate, but vector similarity search is a batch operation (one query against ChromaDB). Solution: pre-compute similarity scores before the pipeline runs, store them in `ScoringContext.similarity_scores`, and the scorer does a simple dict lookup.

```
Engine.generate_recommendations()
  |
  |-- [If AI enabled] SimilarityMatcher.find_similar() → dict[id, score]
  |-- Build ScoringContext (includes pre-computed similarity_scores)
  |-- ScoringPipeline.score_candidates()
  |     |-- GenreMatchScorer
  |     |-- CreatorMatchScorer
  |     |-- TagOverlapScorer
  |     |-- SeriesOrderScorer
  |     |-- RatingPatternScorer
  |     |-- SemanticSimilarityScorer  ← looks up pre-computed score
  |
  |-- Ranker, filtering, formatting (unchanged)
  |-- [LLM reasoning post-processing] (unchanged)
```

### Completed Steps

1. **Added SemanticSimilarityScorer** (`src/recommendations/scorers.py`) ✅
   - Added `similarity_scores: dict[str | None, float]` to `ScoringContext`
   - `SemanticSimilarityScorer` (weight 1.5) looks up pre-computed score from context
   - Returns 0.0 when AI is disabled (empty similarity_scores dict)

2. **Integrated AI Scoring into Pipeline** (`src/recommendations/engine.py`) ✅
   - Similarity scores pre-computed *before* the pipeline via `SimilarityMatcher.find_similar()`
   - Scores passed into `ScoringContext` as `similarity_scores` dict
   - `SemanticSimilarityScorer` conditionally added to pipeline when `embedding_generator` is provided
   - Removed post-pipeline blending block (naive 50/50 average)

3. **Updated Exports and Config** ✅
   - `src/recommendations/__init__.py` exports `SemanticSimilarityScorer`
   - `config/example.yaml` adds `semantic_similarity: 1.5` to scorer_weights

4. **Comprehensive Test Suite** ✅
   - 5 new `SemanticSimilarityScorer` tests in `tests/test_scorers.py`
   - All existing AI-mode and non-AI engine tests pass with new flow

### Files Modified

| File | Action |
|------|--------|
| `src/recommendations/scorers.py` | **Modified** — Added similarity_scores to ScoringContext, added SemanticSimilarityScorer |
| `src/recommendations/engine.py` | **Modified** — Pre-compute similarity before pipeline, conditionally add AI scorer, removed blending |
| `src/recommendations/__init__.py` | **Modified** — Export SemanticSimilarityScorer |
| `config/example.yaml` | **Modified** — Added semantic_similarity weight |
| `tests/test_scorers.py` | **Modified** — 5 new SemanticSimilarityScorer tests |
| `docs/V1_ROADMAP.md` | **Modified** — Marked Phase 4 tasks complete |

### Key Design Decisions

1. **Pre-computed scores**: Batch similarity search happens once before the pipeline, not per-candidate. The scorer is a simple lookup.
2. **Conditional scorer addition**: `SemanticSimilarityScorer` is only appended to the pipeline when `embedding_generator` is not None. Non-AI mode is unaffected.
3. **No AI module imports in scorer**: The scorer reads a plain dict — no dependency on embedding or vector DB modules.
4. **Weight 1.5**: Same as CreatorMatchScorer, lower than GenreMatchScorer (2.0). AI similarity is important but shouldn't dominate.

### Testing

- **486 total tests passing** (5 new)
- All quality checks clean for changed files

---

*Last Updated: 2026-01-27*
