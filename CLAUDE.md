# Claude Code Instructions for Personal Recommendations

## Project Overview

Personal recommendation system that analyzes user ratings/reviews across media types (books, movies, TV shows, video games) using a local LLM via Ollama.

**Key Features:** Multi-source ingestion, cross-content-type recommendations, local LLM (privacy-preserving), dual CLI/web interface, vector-based semantic search.

## Required Reading

Before starting work, read the relevant documentation:

- **README.md** - Project overview, features, usage
- **ARCHITECTURE.md** - System architecture, components, data flow
- **CONTRIBUTING.md** - Development standards for open source contributors
- **QUICKSTART.md** - Getting started guide
- **docs/** - Additional technical docs (MODEL_RECOMMENDATIONS.md, CHROMADB_SETUP.md, PYTHON_VERSION.md, PLUGIN_DEVELOPMENT.md, CUSTOM_RULES.md, SECURITY.md, TROUBLESHOOTING.md)

## Project Structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
├── ingestion/        # Data ingestion
│   └── sources/      # Source plugins (auto-discovered)
├── llm/              # Ollama interaction
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Recommendation engine (scorers, pipeline, ranking)
├── enrichment/       # Background metadata enrichment
├── conversation/     # Conversational AI chat system
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
Exception: `_` for unused variables, `cls` for class methods.

### Code Cleanliness Standards

**These rules are non-negotiable. Every new line of code must follow them. Do not leave cleanup for later — write it clean the first time.**

#### DRY — Don't Repeat Yourself

- **3-strike rule**: If you write the same pattern 3+ times, extract a helper, base class, or data-driven approach. Two is a coincidence; three is a refactor.
- **Use existing utilities**: Before writing extraction/normalization logic, search the codebase — `get_enum_value()`, `extract_and_normalize_genres()`, `ContentType.from_string()`, `get_feature_flags()`, etc. already exist.
- **Data-driven over copy-paste branches**: When multiple branches differ only in table names, column lists, or field mappings, use a config dict and a single code path.
- **Base classes for shared plugin behavior**: Use Template Method pattern (e.g., `ArrPlugin` base for Radarr/Sonarr) instead of duplicating fetch/transform/validate logic.
- **Extract static methods for repeated internal patterns**: Options building, stream chunk iteration, keyword fetching — if two methods share the same 5-line block, extract it.

#### Type Safety

- **No `Any` where a real type exists.** Use `TYPE_CHECKING` imports to avoid circular dependencies while keeping proper types. Every function parameter and return value should have the most specific type possible.
- **Use `from __future__ import annotations`** in modules that need forward references or `TYPE_CHECKING` imports.
- **Use keyword arguments** for non-obvious positional parameters. `save_content_item(item, embedding=emb)` not `save_content_item(item, emb)`.
- **Use `if x is not None:` not `if x:`** when the value could legitimately be `0`, `False`, or empty string.
- **Derive field lists from models**, not hardcoded sets. Use `Model.model_fields` or introspection instead of manually listing field names that will go stale.

#### Dead Code & Defensive Waste

- **Delete unused code immediately.** No backward-compat wrappers, no-op `pass` blocks, commented-out code, or methods that nothing calls. Git has history if you need it back.
- **Don't add defensive `or {}` / `or []`** when the model/dataclass already defaults the field. Trust your own defaults.
- **Don't re-raise without modification**: `except SomeError: raise` is a no-op — remove the try/except entirely.
- **Use `enumerate()`** not `count = 0; count += 1`.
- **Use `upsert()`** instead of get-then-add-or-update when the API supports it.

#### Security Defaults

- **CORS defaults to localhost**, never wildcard. Set `allow_credentials=False` when wildcard origins are used.
- **Never expose internal error details in HTTP responses.** Use generic messages (`"Failed to generate recommendations"`); log the real error server-side with `logger.error()`.
- **Define credentials/constants in one canonical location**, import everywhere else. No duplicate definitions.

#### Mutation & Side Effects

- **Copy dicts/lists before mutating** if the original is passed in from outside. `dict(metadata)` before modifying, not `metadata["key"] = value` on someone else's dict.
- **Set configuration once in `__init__`**, not on every method call (e.g., PRAGMA settings, collection metadata).

#### Imports

- **Module-level imports only.** No `import re` inside a function body. No bottom-of-file import hacks to avoid circularity — use `TYPE_CHECKING` blocks instead.
- **Use `from __future__ import annotations`** when you need `TYPE_CHECKING` imports — it makes all annotations strings and avoids runtime import overhead.

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

**Think before acting.** Do not jump straight into writing code. Ask clarifying questions if requirements are ambiguous, there are multiple valid approaches, or the scope is unclear. It is better to confirm intent upfront than to redo work.

1. Read relevant documentation
2. **Search the codebase for existing patterns** — before writing anything, look at how similar features are already implemented (e.g., grep for analogous endpoints, UI components, parsers). Match the conventions you find rather than inventing new ones.
3. Ask questions if anything is unclear or if there are trade-offs to decide
4. Write tests first (TDD recommended)
5. Implement following existing patterns
6. Ensure all checks pass
7. **Update documentation** — every change set must include documentation updates. Check ARCHITECTURE.md, README.md, QUICKSTART.md, CLAUDE.md, and relevant docs/ files. If the change adds, removes, or modifies user-facing behavior, configuration, or system components, the docs must reflect it before the work is considered done.
8. Commit with proper message format

## Adding New Data Sources

1. Create parser in `src/ingestion/sources/`
2. Follow existing patterns (goodreads.py, steam.py)
3. Yield `ContentItem` objects
4. Add comprehensive tests with mocked APIs
5. Update CLI/web to support new source
6. **Update docs**: Add to ARCHITECTURE.md sources list, README.md data sources table, config/example.yaml, and docs/PLUGIN_DEVELOPMENT.md reference list

## Bug Fixes

1. Write regression test first (fails before fix, passes after)
2. Fix the bug
3. Document bug in test docstring
4. **Update docs** if the fix changes behavior, configuration, or corrects something documented incorrectly
5. Commit with `fix` type

## Documentation Maintenance

**Documentation is not optional.** Every change set must leave the docs accurate. This is not a separate step to do later — it is part of completing the work.

**What to check on every change:**
- `README.md` — if user-facing behavior, data sources, or configuration changed
- `ARCHITECTURE.md` — if system components, data flow, or tech stack changed
- `QUICKSTART.md` — if setup steps or getting-started workflow changed
- `CLAUDE.md` — if development standards, project structure, or workflows changed
- `config/example.yaml` — if configuration options were added or modified
- Relevant `docs/*.md` — if the change touches areas covered by specific docs

**For documentation-only changes**, use the `docs:` commit type.

## Pre-commit Checklist

- [ ] All tests pass: `pytest`
- [ ] Formatting: `black --check src/ tests/`
- [ ] Type checking: `mypy src/`
- [ ] Linting: `ruff check src/ tests/`
- [ ] No hardcoded values
- [ ] No references to config/config.yaml
- [ ] No abbreviated variable names (`e`, `i`, `msg`, `cfg`, etc.)
- [ ] No `Any` types where a real type exists (use `TYPE_CHECKING` if needed)
- [ ] No duplicated logic — extracted to shared helpers/base classes
- [ ] No dead code, backward-compat wrappers, or no-op blocks
- [ ] No `detail=str(error)` in HTTP exceptions — use generic messages
- [ ] Error handling appropriate
- [ ] Keyword arguments used for non-obvious parameters
- [ ] **Documentation is accurate** — all affected docs updated (see Documentation Maintenance above)