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
│   ├── cli/               # Command-line interface (to be implemented)
│   ├── web/               # Web interface (to be implemented)
│   ├── ingestion/         # Data ingestion
│   │   └── sources/       # Source-specific parsers
│   │       └── goodreads.py  # ✅ Implemented
│   ├── llm/               # LLM interaction (to be implemented)
│   ├── storage/           # Storage layer (to be implemented)
│   ├── recommendations/  # Recommendation engine (to be implemented)
│   ├── models/            # Data models
│   │   └── content.py     # ✅ Implemented
│   └── utils/             # Utility functions
├── tests/                 # Test suite
│   └── test_goodreads_parser.py  # ✅ Implemented
├── inputs/                # Input data files
│   └── goodreads_library_export.csv  # Your Goodreads data
├── config/                # Configuration files
│   └── example.yaml       # ✅ Example config
└── docs/                  # Additional documentation
```

## Current Status

### ✅ Completed
- Project structure and documentation
- Goodreads CSV parser
- Content data models
- Test suite setup
- Linting and formatting configuration

### 🚧 Next Steps (To Be Implemented)
1. **Storage Layer**
   - SQLite database for structured data
   - ChromaDB for vector embeddings
   - Data persistence logic

2. **LLM Integration**
   - Ollama client wrapper
   - Embedding generation
   - Prompt templates
   - Recommendation generation

3. **CLI Interface**
   - Command parsing
   - Recommendation commands
   - Update commands

4. **Web Interface**
   - FastAPI server setup
   - REST API endpoints
   - Simple web UI

5. **Recommendation Engine**
   - Preference analysis
   - Similarity matching
   - Ranking algorithm

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
