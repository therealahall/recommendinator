# Quick Start Guide

This guide will help you get started with the Personal Recommendations system.

## Prerequisites

1. **Python 3.11+** installed
2. **Ollama** installed and running
3. At least one Ollama model pulled (e.g., `mistral:7b`)

## Initial Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development
   ```

2. **Set up configuration:**
   ```bash
   cp config/example.yaml config/config.yaml
   # Edit config/config.yaml with your preferences
   ```

3. **Verify Ollama is running:**
   ```bash
   ollama serve
   # In another terminal:
   ollama list
   ```

4. **Run tests to verify setup:**
   ```bash
   pytest
   ```

## Project Structure Overview

```
personal-recommendations/
├── src/                    # Source code
│   ├── cli/               # Click CLI interface
│   │   ├── commands.py    # recommend, update, complete, preferences
│   │   ├── config.py      # Config loading and scorer construction
│   │   └── main.py        # CLI entry point
│   ├── web/               # FastAPI web interface
│   │   ├── api.py         # REST API endpoints
│   │   ├── app.py         # FastAPI app factory
│   │   └── state.py       # Application state
│   ├── ingestion/         # Data ingestion
│   │   └── sources/       # Source parsers (goodreads, steam, sonarr, radarr, CSV, JSON, Markdown)
│   ├── llm/               # Ollama interaction (optional AI layer)
│   ├── storage/           # SQLite + ChromaDB (vector DB optional)
│   ├── recommendations/   # Recommendation engine
│   │   ├── scorers.py     # Scorer classes and weight override helpers
│   │   ├── scoring_pipeline.py
│   │   ├── engine.py      # Main recommendation engine
│   │   ├── preferences.py # Preference analysis
│   │   └── ranking.py     # Ranking and adjustments
│   ├── models/            # Data models
│   │   ├── content.py     # ContentItem, ContentType, ConsumptionStatus
│   │   └── user_preferences.py  # UserPreferenceConfig
│   └── utils/             # Utility functions
├── tests/                 # Test suite (mirrors src/ structure)
├── inputs/                # Input data files
├── config/                # Configuration files
│   └── example.yaml       # Example config (use for tests)
└── docs/                  # Additional documentation
```

## Current Status

All core phases (0-5) are complete:
- Multi-source data ingestion (Goodreads, Steam, Sonarr, Radarr, CSV, JSON, Markdown)
- SQLite + optional ChromaDB storage with multi-user support
- Non-AI scoring pipeline (genre, creator, tag, series, rating scorers)
- AI enhancement layer (semantic similarity, LLM reasoning) - optional
- Per-user preferences (configurable scorer weights, stored in DB)
- CLI and REST API interfaces

## Development Workflow

1. **Make changes:**
   ```bash
   # Create a feature branch
   git checkout -b feat/your-feature-name
   ```

2. **Write tests:**
   ```bash
   # Write tests first (TDD)
   # Add tests in tests/
   ```

3. **Run checks:**
   ```bash
   make check  # Runs format-check, lint, type-check, and tests
   ```

4. **Format code:**
   ```bash
   make format  # Auto-format with black
   ```

5. **Commit:**
   ```bash
   # Use conventional commits
   git commit -m "feat(module): description"
   ```

## Testing the Goodreads Parser

You can test the Goodreads parser with your actual data:

```python
from pathlib import Path
from src.ingestion.sources.goodreads import parse_goodreads_csv

# Parse your Goodreads export
items = list(parse_goodreads_csv(Path("inputs/goodreads_library_export.csv")))

print(f"Parsed {len(items)} books")
for item in items[:5]:  # Show first 5
    print(f"- {item.title} by {item.author} ({item.status})")
```

## Common Commands

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=src --cov-report=html

# Format code
black src/ tests/

# Type check
mypy src/

# Lint
ruff check src/ tests/

# Run all checks
make check
```

## Getting Help

- See [README.md](README.md) for project overview
- See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
- See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines
