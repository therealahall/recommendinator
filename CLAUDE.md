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
│   └── sources/      # Source parsers (goodreads.py, steam.py, sonarr.py, radarr.py, etc.)
├── llm/              # Ollama interaction
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Recommendation engine (scorers, pipeline, ranking)
├── models/           # Data models (ContentItem, ContentType, UserPreferenceConfig)
└── utils/            # Utility functions
tests/                # Mirrors src/ structure
config/               # Configuration files (example.yaml for tests)
```

## Development Standards

### Running Commands

- **Never use `cd` in front of commands.** The workspace path is already the project root.
- **Never pipe test output or use head, tail, etc.** Run each command directly:
  - `python3.11 -m pytest tests/` (not `pytest | head` or similar)
  - `python3.11 -m black --check src/ tests/`
  - `python3.11 -m mypy src/`
  - `python3.11 -m ruff check src/ tests/`

### Python Version

**Always use `python3.11` for all commands.** Do not use bare `python` or `python3`.

### Code Quality (ALL must pass — always green)

**The codebase must always be in a clean state.** All four quality tools must pass at all times — not just before commits, but after every change. If pre-existing code has issues, fix them immediately. Never leave the codebase in a failing state.

```bash
python3.11 -m pytest          # All tests pass
python3.11 -m black --check src/ tests/     # Formatting
python3.11 -m mypy src/                     # Type checking (strict)
python3.11 -m ruff check src/ tests/        # Linting
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

### Regression Testing for User-Reported Bugs

**CRITICAL: When a user reports a bug, ALWAYS write a regression test:**

1. **Before fixing**: Write a test that reproduces the bug (should fail)
2. **Document the bug**: Include in the test docstring:
   - What was reported ("Bug reported: ...")
   - Root cause analysis ("Root cause: ...")
   - The fix applied ("Fix: ...")
3. **After fixing**: Verify the test passes
4. **Naming**: Use descriptive names ending in `_regression` (e.g., `test_series_book_2_not_recommended_regression`)
5. **Location**: Add to relevant test file in a `Test*Regression` class

Example structure:
```python
class TestSeriesOrderingRegression:
    """Regression tests for series ordering bugs."""

    def test_series_book_2_not_recommended_when_book_1_unread_regression(self):
        """Regression test: Book #2 should not be recommended when #1 is unread.

        Bug reported: "The Black Unicorn #2" was recommended when user
        hadn't read book #1.

        Root cause: Engine fetched only 100 items; book #1 was position 171.

        Fix: Removed the 100-item limit for series checking.
        """
        # Test implementation...
```

This ensures bugs don't resurface and documents the project's bug history.

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

## Configuration Documentation Requirements

**CRITICAL: When adding or modifying configuration values:**

1. **Update `config/example.yaml`** with the new option, including:
   - Clear comments explaining what the option does
   - Sensible default value
   - Valid values/ranges where applicable

2. **Update user documentation** in one or more of:
   - `README.md` - if it affects basic usage
   - `QUICKSTART.md` - if users need to know about it to get started
   - Relevant `docs/*.md` file - for detailed technical docs

3. **Update code documentation**:
   - Docstrings where the config is read/used
   - Type hints for config structures

This ensures users can discover and understand all configuration options.

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