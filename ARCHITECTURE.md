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

Core logic for generating recommendations.

**Process:**
1. Analyze user's consumed content (ratings, reviews)
2. Extract preferences and patterns
3. Find unconsumed content similar to high-rated items
4. Generate ranked recommendations with reasoning

**Filtering Logic:**
- Separate consumed vs unconsumed based on data files
- Handle explicit completion updates
- Consider content type, genre, length, etc.

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
Storage Layer (persist to DB + vector DB)
    ↓
LLM Layer (generate embeddings, analyze preferences)
    ↓
Recommendation Engine (match preferences to unconsumed content)
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

- Multi-user support (if needed)
- Advanced filtering (genre, length, etc.)
- Recommendation explanations
- Export/import functionality
- Integration with external APIs (optional)
