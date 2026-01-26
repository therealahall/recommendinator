# Claude Code Instructions for Personal Recommendations

## Project Overview

Personal recommendation system that analyzes user ratings/reviews across media types (books, movies, TV shows, video games) using a local LLM via Ollama.

**Key Features:** Multi-source ingestion, cross-content-type recommendations, local LLM (privacy-preserving), dual CLI/web interface, vector-based semantic search.

## Required Reading

Before starting work, read the relevant documentation:

- **README.md** - Project overview, features, usage
- **ARCHITECTURE.md** - System architecture, components, data flow
- **CONTRIBUTING.md** - Development standards, code style, testing
- **DEVELOPMENT.md** - Development log, decisions, implementation history
- **QUICKSTART.md** - Getting started guide
- **docs/** - Additional technical docs (MODEL_RECOMMENDATIONS.md, CHROMADB_SETUP.md, SCHEMA_DESIGN.md, PYTHON_VERSION.md)

## Project Structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
├── ingestion/        # Data ingestion
│   └── sources/      # Source parsers (goodreads.py, steam.py)
├── llm/              # Ollama interaction
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Recommendation engine
├── models/           # Data models (ContentItem, ContentType, ConsumptionStatus)
└── utils/            # Utility functions
tests/                # Mirrors src/ structure
config/               # Configuration files (example.yaml for tests)
```

## Development Standards

### Code Quality (ALL must pass before commit)

```bash
pytest                        # All tests pass
black --check src/ tests/     # Formatting
mypy src/                     # Type checking (strict)
ruff check src/ tests/        # Linting
```

Or use the Makefile: `make check`

### Naming Conventions

**Do not use abbreviated variable names.** Use clear, descriptive names:

```python
# CORRECT
for item, item_embedding in zip(items, embeddings, strict=True):
    storage_manager.save_content_item(item, embedding=item_embedding)

# WRONG - abbreviated names
for i, emb in zip(items, embeddings):
    storage_manager.save_content_item(i, embedding=emb)
```

Avoid: `i`, `j`, `e`, `emb`, `ct`, `cfg`, single letters. Use full words.

### Testing Requirements

- **80%+ coverage target**
- **ALL functionality must have tests**
- Mock external dependencies (Ollama API, file I/O, Steam API)
- Never make real network requests in tests
- Regression tests required for bug fixes

### Commit Conventions

Follow **Conventional Commits**:

```
<type>(<scope>): <subject>

Types: feat, fix, docs, style, refactor, test, chore
```

**Atomic commits**: Break changes into logical, focused commits. Separate schema → implementation → tests → docs.

## Security

**NEVER use `config/config.yaml`** - contains secrets (API keys, Steam IDs).

Always use `config/example.yaml` for tests and examples:

```python
# CORRECT
config = load_config(Path("config/example.yaml"))

# WRONG - NEVER DO THIS
config = load_config(Path("config/config.yaml"))
```

## Technology Stack

- **Python**: 3.11+ (3.14.2 available, 3.11 recommended for ChromaDB)
- **LLM**: Ollama (local, AMD-compatible)
- **Vector DB**: ChromaDB
- **SQL DB**: SQLite
- **Web**: FastAPI
- **CLI**: Click
- **Testing**: pytest
- **Quality**: Black, MyPy (strict), Ruff

## Architecture Principles

1. **Separation of Concerns**: Keep ingestion, LLM, storage separate
2. **Testability**: Design for easy mocking
3. **Extensibility**: Easy to add new data sources/content types
4. **Configuration**: No hardcoded values
5. **Error Handling**: Graceful with clear messages

## Adding New Features

1. Read relevant documentation
2. Write tests first (TDD recommended)
3. Implement following existing patterns
4. Ensure all checks pass
5. Update documentation if needed
6. Commit with proper message format

## Adding New Data Sources

1. Create parser in `src/ingestion/sources/`
2. Follow existing patterns (goodreads.py, steam.py)
3. Yield `ContentItem` objects
4. Add comprehensive tests with mocked APIs
5. Update CLI/web to support new source

## Bug Fixes

1. Write regression test first (fails before fix, passes after)
2. Fix the bug
3. Document bug in test docstring
4. Commit with `fix` type

## Documentation Maintenance

When changing paradigms or patterns:
1. Update relevant documentation files
2. Update CLAUDE.md and .cursorrules to stay in sync
3. Use `docs:` commit type

## Pre-commit Checklist

- [ ] All tests pass: `pytest`
- [ ] Formatting: `black --check src/ tests/`
- [ ] Type checking: `mypy src/`
- [ ] Linting: `ruff check src/ tests/`
- [ ] No hardcoded values
- [ ] No references to config/config.yaml
- [ ] Error handling appropriate
- [ ] Type hints used
- [ ] Documentation updated if needed