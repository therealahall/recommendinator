# V1 Roadmap

This document outlines the implementation plan for v1 of the Personal Recommendations system.

**Created:** 2026-01-25
**Status:** In Progress

---

## Vision

A personal recommendation system that:
- Ingests content from multiple sources (Goodreads, Steam, Plex, Sonarr, Radarr, etc.)
- Tracks consumed vs unconsumed (wishlist) content across books, movies, TV shows, and video games
- Provides intelligent recommendations using cross-content-type preferences
- **Works without AI** - AI is an optional enhancement, not a requirement
- Supports multiple users (designed in from day 1)
- Uses a plugin architecture for extensible data sources

---

## Core Requirements (v1)

| Requirement | Description |
|-------------|-------------|
| Curated wishlist model | Unconsumed = "things I want to consume" (not discovery) |
| Season-level TV tracking | Track seasons, not individual episodes |
| Normalized 1-5 ratings | All sources normalized to common scale |
| Cross-content influence | Sci-fi books → boost sci-fi game recommendations |
| Series-aware | Only recommend next unstarted/uncontinued item in series |
| Manual sync trigger | Sync sources from web UI |
| Hybrid scoring | Content-based + rule-based scoring pipeline |
| AI optional | Full functionality without LLM/embeddings |
| Multi-user ready | Schema supports multiple users |
| Plugin architecture | Formalized interface for data sources |

---

## Stretch Goals (post-v1)

- Discovery mode (surface things you didn't know about)
- Interactive refinement ("I'm burnt out on sci-fi")
- Scheduled sync (cron-style)
- Cloud LLM support (OpenAI, Claude API)
- Mood/context-aware recommendations

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Interface Layer                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────┐  │
│  │   Web UI    │  │    CLI      │  │   REST API                  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                      Recommendation Engine                           │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Scoring Pipeline                             │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │ │
│  │  │ Content-Based│  │  Rule-Based  │  │   AI Enhancement      │ │ │
│  │  │   Scoring    │  │   Scoring    │  │   (optional)          │ │ │
│  │  │              │  │              │  │                       │ │ │
│  │  │ • Genre match│  │ • Series     │  │ • Semantic similarity │ │ │
│  │  │ • Tag overlap│  │ • Recency    │  │ • LLM reasoning       │ │ │
│  │  │ • Author/dev │  │ • Variety    │  │ • NL preferences      │ │ │
│  │  │ • Rating pat.│  │ • User rules │  │                       │ │ │
│  │  └──────────────┘  └──────────────┘  └───────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                │                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │              User Preferences Store                              ││
│  │   • Weights configuration    • Natural language rules (if AI)   ││
│  │   • Content type preferences • Recent consumption history       ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                        Data Layer                                    │
│  ┌─────────────────────┐  ┌─────────────────────┐                   │
│  │   SQLite (required) │  │ ChromaDB (optional) │                   │
│  │                     │  │                     │                   │
│  │ • Content items     │  │ • Embeddings        │                   │
│  │ • Users             │  │ • Semantic search   │                   │
│  │ • Ratings           │  │                     │                   │
│  │ • Consumption status│  │ (only if AI enabled)│                   │
│  │ • Sync state        │  │                     │                   │
│  │ • User preferences  │  │                     │                   │
│  └─────────────────────┘  └─────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                       Ingestion Layer                                │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │                    Source Plugin Interface                       ││
│  │   • fetch() - retrieve data from source                         ││
│  │   • parse() - convert to ContentItem                            ││
│  │   • normalize_rating() - convert to 1-5 scale                   ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │Goodreads │ │  Steam   │ │  Plex    │ │ Sonarr   │ │  Radarr  │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │   GOG    │ │   Epic   │ │ Nintendo │ │Custom CSV│  ...         │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 0: Foundation Reset

**Goal:** Clean slate with proper project structure for v1

**Tasks:**
- [x] Reset databases (delete data/*.db, data/chroma_db)
- [x] Update schema to v1 with `users` table and `user_id` foreign keys
- [x] Create configuration for AI on/off toggle
- [x] Update documentation with v1 architecture (V1_ROADMAP.md)
- [x] Set up feature flags system (simple config-based)

**Schema Changes:**
```sql
-- New users table
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settings JSON  -- Per-user settings (AI enabled, weights, etc.)
);

-- Add user_id FK to content_items
ALTER TABLE content_items ADD COLUMN user_id INTEGER REFERENCES users(id);

-- Default user created on first run
INSERT INTO users (id, username, display_name) VALUES (1, 'default', 'Default User');
```

**Config Addition:**
```yaml
features:
  ai_enabled: true  # Master toggle for all AI features
  embeddings_enabled: true  # Vector similarity (requires ai_enabled)
  llm_recommendations_enabled: true  # LLM reasoning (requires ai_enabled)
```

**Deliverable:** Can run migrations, create users, feature flags work

---

### Phase 1: Core Data Layer

**Goal:** Solid storage foundation with multi-user support

**Tasks:**
- [x] Update ContentItem model with `user_id`
- [x] Refactor SQLiteDB for user-scoped queries
- [x] Make ChromaDB truly optional (lazy initialization)
- [x] Create StorageManager that works without vector DB
- [x] Add user management functions
- [x] Update all tests

**Key Changes:**
```python
# src/models/content.py
class ContentItem(BaseModel):
    user_id: int = 1  # Default user
    # ... existing fields

# src/storage/sqlite_db.py
def get_content_items(self, user_id: int, ...) -> list[ContentItem]:
    # All queries scoped by user_id

# src/storage/manager.py
class StorageManager:
    def __init__(self, db_path, vector_db_path=None, ai_enabled=False):
        self.sqlite = SQLiteDB(db_path)
        self.vector_db = None
        if ai_enabled and vector_db_path:
            self.vector_db = VectorDB(vector_db_path)
```

**Deliverable:** Can CRUD content items per user, works without ChromaDB

---

### Phase 2: Ingestion Framework

**Goal:** Formalized plugin system for data sources

**Tasks:**
- [x] Define `SourcePlugin` abstract interface
- [x] Refactor Goodreads parser to implement interface
- [x] Refactor Steam parser to implement interface
- [x] Create plugin registry/discovery system
- [x] Add rating normalization to interface
- [ ] Handle source-of-truth conflicts (configurable)
- [ ] Create Markdown ingestion system
- [ ] Create generic CSV ingestion system
- [ ] Create generic JSON ingestion system
- [ ] Create Sonarr ingestion system
- [ ] Create Radarr ingestion system

**Plugin Interface:**
```python
# src/ingestion/plugin.py
from abc import ABC, abstractmethod
from typing import Iterator
from src.models.content import ContentItem, ContentType

class SourcePlugin(ABC):
    """Base class for all data source plugins."""

    name: str  # e.g., "goodreads", "steam"
    content_types: list[ContentType]  # What types this source provides
    requires_api_key: bool = False

    @abstractmethod
    def fetch(self, config: dict) -> Iterator[ContentItem]:
        """Fetch and parse content from source."""
        pass

    @abstractmethod
    def normalize_rating(self, raw_rating: any) -> int | None:
        """Convert source rating to 1-5 scale."""
        pass

    @classmethod
    def get_config_schema(cls) -> dict:
        """Return JSON schema for required config fields."""
        pass
```

**Deliverable:** Can list available plugins, import from Goodreads/Steam, ratings normalized

---

### Phase 3: Non-AI Recommendation Engine

**Goal:** Fully functional recommendations without any AI

**Tasks:**
- [ ] Build genre/tag extraction from metadata
- [ ] Create content-based scorer (genre overlap, author match)
- [ ] Create rule-based scorer (series logic, recency, variety)
- [ ] Build scoring pipeline that combines scorers
- [ ] Implement configurable weights
- [ ] Keep existing series filtering logic

**Scorer Architecture:**
```python
# src/recommendations/scorers/base.py
class Scorer(ABC):
    weight: float = 1.0

    @abstractmethod
    def score(self, candidate: ContentItem, context: ScoringContext) -> float:
        """Return score 0.0-1.0 for this candidate."""
        pass

# Scorers to implement:
# - GenreMatchScorer: Score based on genre overlap
# - AuthorMatchScorer: Score based on author/developer preferences
# - TagOverlapScorer: Score based on tag/theme overlap
# - SeriesOrderScorer: Boost first-in-series, penalize out-of-order
# - RatingPatternScorer: Boost similar to 5-star, penalize similar to 1-star
```

**Deliverable:** Can get recommendations without LLM using genre/tag matching and rules

---

### Phase 4: AI Enhancement Layer (Optional)

**Goal:** AI features that enhance (but aren't required by) the engine

**Tasks:**
- [ ] Create AI scorer using embeddings for semantic similarity
- [ ] Make embedding generation conditional on `ai_enabled`
- [ ] Add LLM reasoning generator (optional post-processing)
- [ ] Integrate AI scorer into pipeline when enabled
- [ ] Natural language preference interpreter (for Phase 5)

**Key Design:**
```python
# src/recommendations/scorers/ai_scorer.py
class SemanticSimilarityScorer(Scorer):
    """Score based on embedding similarity. Requires AI enabled."""

    def __init__(self, vector_db: VectorDB, embedding_generator: EmbeddingGenerator):
        self.vector_db = vector_db
        self.embedding_generator = embedding_generator
```

**Deliverable:** Recommendations work with AI off, enhanced scores with AI on

---

### Phase 5: User Preferences System

**Goal:** Configurable personal rules per user

**Tasks:**
- [ ] Create preferences storage (in users.settings JSON or separate table)
- [ ] Build simple preference types (toggles, sliders)
- [ ] Add natural language preference interpreter (requires AI)
- [ ] Create preference-to-scorer mapping
- [ ] Web UI for preference management

**Preference Types:**
```python
@dataclass
class UserPreferenceConfig:
    # Simple toggles
    series_in_order: bool = True
    variety_after_completion: bool = False

    # Weights (0.0-1.0)
    genre_match_weight: float = 0.3
    author_match_weight: float = 0.2

    # Content-type specific
    min_book_pages: int | None = None
    max_movie_runtime: int | None = None

    # Natural language rules (requires AI to interpret)
    custom_rules: list[str] = []
```

**Deliverable:** Can save/load preferences per user, preferences affect recommendations

---

### Phase 6: Web Interface

**Goal:** Full workflow accessible via browser

**Tasks:**
- [ ] Add source sync trigger endpoints
- [ ] Add user management UI (if multi-user)
- [ ] Add preferences management UI
- [ ] Improve recommendations display
- [ ] Add "mark as completed" from recommendations
- [ ] Show score breakdown (why recommended)

**New Endpoints:**
```python
POST /api/sync/{source_name}  # Trigger sync for a source
GET  /api/sources             # List available plugins
GET  /api/preferences         # Get user preferences
PUT  /api/preferences         # Update user preferences
GET  /api/recommendations/{content_type}?include_reasoning=true
```

**Deliverable:** Full workflow via browser - sync, configure, get recommendations

---

### Phase 7: Polish & Documentation

**Goal:** Ready for open source

**Tasks:**
- [ ] Create Dockerfile and docker-compose.yml
- [ ] Write user documentation (setup guide)
- [ ] Write plugin development guide
- [ ] Add example custom preference rules
- [ ] Performance testing with larger datasets
- [ ] Security review (no secrets in code)
- [ ] License selection

**Deliverable:** Ready for others to use

---

## Progress Tracking

| Phase | Status | Started | Completed |
|-------|--------|---------|-----------|
| Phase 0: Foundation Reset | Complete | 2026-01-25 | 2026-01-25 |
| Phase 1: Core Data Layer | Complete | 2026-01-25 | 2026-01-25 |
| Phase 2: Ingestion Framework | In Progress | 2026-01-26 | - |
| Phase 3: Non-AI Engine | Not Started | - | - |
| Phase 4: AI Enhancement | Not Started | - | - |
| Phase 5: User Preferences | Not Started | - | - |
| Phase 6: Web Interface | Not Started | - | - |
| Phase 7: Polish | Not Started | - | - |

---

## Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite as primary store | Simple, portable, no external dependencies |
| ChromaDB optional | Only initialized if AI features enabled |
| Plugin interface for sources | Community can contribute, each plugin self-contained |
| User table from day 1 | Even if v1 is single-user, schema supports multi-user |
| Scoring pipeline | Each scorer independent, easy to add/remove/weight |
| Preferences in DB | Portable, UI-editable, per-user in multi-user mode |

---

## What Changes from Current Codebase

1. **Decouple AI from core engine** - Current engine assumes embeddings exist
2. **Add user model** - Schema needs `user_id` on relevant tables
3. **Formalize plugin interface** - Current sources are ad-hoc Python files
4. **Build non-AI scoring pipeline** - Genre/tag matching, rule-based scoring
5. **User preferences system** - Doesn't exist yet
6. **Web UI sync trigger** - Add button to trigger source sync

---

*This document will be updated as implementation progresses.*