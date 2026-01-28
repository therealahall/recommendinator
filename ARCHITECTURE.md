# Architecture Documentation

## Overview

The Personal Recommendations system is designed to ingest data from multiple sources, process it through a local LLM (via Ollama), and generate personalized recommendations. The architecture emphasizes modularity, testability, and extensibility.

## System Components

### 1. Data Ingestion Layer (`src/ingestion/`)

Responsible for parsing and normalizing data from various sources.

**Current Sources:**
- Goodreads CSV exports
- (Future: Game reviews repository, Letterboxd, etc.)

**Responsibilities:**
- Parse different file formats (CSV, JSON, TXT, Markdown)
- Normalize data into common schemas
- Extract key fields (title, rating, review, completion status)
- Handle different content types (books, movies, games, TV)

**Design:**
- Plugin-based architecture for new sources
- Each source has its own parser module
- Common data models for normalized output

### 2. Storage Layer (`src/storage/`)

Manages persistent storage of processed data and embeddings.

**Storage Strategy:**
- **Vector Database**: For storing embeddings of reviews and content descriptions (enables semantic search)
  - Recommended: ChromaDB (lightweight, local-first)
  - Alternative: FAISS, Qdrant
- **SQLite Database**: For structured data (ratings, metadata, completion status)
- **File-based Cache**: For raw ingested data and processing state

**Data Models:**
- Content items (books, movies, etc.)
- Ratings and reviews
- User consumption status
- LLM-generated embeddings

### 3. LLM Interaction Layer (`src/llm/`)

Handles communication with Ollama and prompt engineering.

**Responsibilities:**
- Generate embeddings for content and reviews
- Create recommendation prompts
- Parse LLM responses
- Manage conversation context

**Model Selection:**
- Default: `mistral:7b` (good balance of quality and performance)
- Configurable via config file
- Supports model switching for different tasks

**Prompt Engineering:**
- System prompts for recommendation generation
- Context building from user's consumption history
- Rating and review analysis prompts

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
  |     |-- [SemanticSimilarityScorer]  (when AI enabled)
  |
  |-- UserPreferenceConfig (optional per-user weight overrides)
  |-- Ranker (adaptation bonus, series bonus, preference adjustments)
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
6. Apply series filtering and ranking adjustments
7. Generate ranked recommendations with reasoning

**Cross-Content-Type Recommendations:**
- Preferences from all content types influence recommendations
- Example: If you've read sci-fi books, the system may recommend sci-fi TV shows (The Expanse) or games (Mass Effect)
- Metadata-based matching (genre/creator overlap) works without AI
- Optional vector embeddings for semantic similarity across content types
- Genre preferences extracted from books, games, TV shows, and movies

**Filtering Logic:**
- Separate consumed vs unconsumed based on data files
- Handle explicit completion updates
- Consider content type, genre, length, etc.
- Series tracking (content-type specific, e.g., book series)

### 5. Interface Layer

#### CLI (`src/cli/`)
- Command-line interface for recommendations and updates
- Uses Click or argparse
- Supports batch operations

#### Web (`src/web/`)
- Flask or FastAPI web server
- REST API endpoints
- Simple web UI for recommendations
- Internal network only (no external exposure)

## Data Flow

```
Input Files (CSV/JSON/etc.)
    ↓
Ingestion Layer (parse & normalize)
    ↓
Storage Layer (persist to SQLite; optionally ChromaDB if AI enabled)
    ↓
Recommendation Engine
    ├── Scoring Pipeline (always: genre, creator, tag, series, rating scorers)
    ├── [AI: vector similarity blending]  ← optional, when AI enabled
    ├── Ranker (adaptation bonus, series bonus, preferences)
    └── [AI: LLM reasoning]              ← optional, when AI enabled
    ↓
Interface Layer (CLI/Web) → User
```

## Update Mechanisms

### 1. File-based Updates
- Monitor input files for changes
- Re-process changed files
- Update storage incrementally

### 2. Explicit Updates
- User marks content as completed via CLI/web
- Store update in database
- Trigger re-analysis if needed

### 3. Batch Updates
- Full reprocessing of all sources
- Useful for initial setup or major changes

## Configuration

Configuration files in `config/`:
- `config.yaml`: Main configuration
  - Ollama model selection
  - Storage paths
  - API endpoints
  - Content type preferences

## Extension Points

### Adding New Data Sources
1. Create parser in `src/ingestion/sources/`
2. Implement common interface
3. Map to content type models
4. Add tests

### Adding New Content Types
1. Extend content type enum
2. Add type-specific recommendation logic
3. Update data models
4. Add type-specific prompts

### Adding New LLM Models
1. Update Ollama client configuration
2. Adjust prompt templates if needed
3. Test with new model

## Technology Stack

- **Python**: 3.11+
- **LLM**: Ollama (local)
- **Vector DB**: ChromaDB (recommended)
- **SQL Database**: SQLite
- **Web Framework**: FastAPI (recommended) or Flask
- **CLI Framework**: Click
- **Testing**: pytest
- **Linting**: Black, MyPy, Ruff

## Performance Considerations

- Initial processing may be slow (acceptable for personal use)
- Vector database enables fast similarity searches
- Caching of LLM responses where appropriate
- Incremental updates preferred over full reprocessing

## Security & Privacy

- All processing happens locally
- No external API calls (except Ollama, which is local)
- Web interface only accessible on internal network
- No user data leaves the machine

## Future Enhancements

- Web UI for preference management
- Natural language preference interpreter (AI-powered)
- Content constraint enforcement (min pages, max runtime)
- Discovery mode (surface things you didn't know about)
- Interactive refinement ("I'm burnt out on sci-fi")
- Scheduled sync (cron-style)
- Export/import functionality
