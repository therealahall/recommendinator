# Contributing Guidelines

Thank you for your interest in contributing to Personal Recommendations! This document covers everything you need to get started.

## Getting Started

1. **Fork and clone** the repository
2. **Install Python 3.11** (required for ChromaDB compatibility)
3. **Install dependencies:**
   ```bash
   python3.11 -m pip install -r requirements.txt
   python3.11 -m pip install -r requirements-dev.txt
   ```
4. **Run the test suite** to verify your setup:
   ```bash
   python3.11 -m pytest
   ```

## Development Workflow

1. Create a branch for your change
2. Make your changes following the standards below
3. Ensure all checks pass (see [Quality Checks](#quality-checks))
4. Submit a pull request

## Quality Checks

**All four checks must pass before submitting a PR:**

```bash
python3.11 -m pytest                       # All tests pass
python3.11 -m black --check src/ tests/    # Formatting
python3.11 -m mypy src/                    # Type checking (strict)
python3.11 -m ruff check src/ tests/       # Linting
```

Or use the Makefile: `make check`

**Important:** Always use `python3.11` explicitly — not bare `python` or `python3`.

## Code Standards

### Formatting & Style
- **Black** for code formatting (default settings)
- **MyPy** in strict mode for type checking
- **Ruff** for linting
- Google-style docstrings
- Type hints on all function signatures

### Naming Conventions

Use clear, descriptive variable names. Do not abbreviate:

```python
# Good
for item, item_embedding in zip(items, embeddings, strict=True):
    storage_manager.save_content_item(item, embedding=item_embedding)

# Bad
for i, emb in zip(items, embeddings):
    storage_manager.save_content_item(i, embedding=emb)
```

Avoid: `i`, `j`, `e`, `emb`, `ct`, `cfg`, single letters. Exception: `_` for unused variables, `cls` for class methods.

### Code Cleanliness

These standards are enforced strictly. Write clean code the first time — don't leave cleanup for later.

- **DRY**: If you write the same pattern 3+ times, extract a helper or base class. Search the codebase for existing utilities before writing new ones (`get_enum_value()`, `extract_and_normalize_genres()`, `get_feature_flags()`, etc.).
- **No `Any` types** where a real type exists. Use `TYPE_CHECKING` imports to avoid circular dependencies while keeping proper types.
- **Use keyword arguments** for non-obvious parameters: `save_item(item, embedding=emb)` not `save_item(item, emb)`.
- **Use `if x is not None:`** not `if x:` when the value could be `0`, `False`, or empty string.
- **Delete dead code** — no backward-compat wrappers, no-op blocks, or methods nothing calls.
- **Don't add defensive `or {}`** when model fields already have defaults.
- **Copy dicts before mutating** if the original was passed in from outside.
- **Module-level imports only** — no inline `import` inside functions. Use `TYPE_CHECKING` blocks instead of bottom-of-file import hacks.
- **Never expose internal errors** in HTTP responses — use generic messages and log details server-side.
- **Data-driven patterns** over copy-paste branches when multiple code paths differ only in names/mappings.

## Testing

### Requirements
- **80%+ coverage target**
- All new functionality must have tests
- Place tests in `tests/` mirroring the `src/` structure
- Mock all external dependencies (Ollama, Steam API, file I/O, etc.)
- Never make real network requests in tests

### Running Tests
```bash
python3.11 -m pytest                              # All tests
python3.11 -m pytest tests/test_web_api.py -v     # Specific file
python3.11 -m pytest --cov=src --cov-report=html  # With coverage
```

### Regression Tests

When fixing a bug, always write a regression test:

1. Write a test that reproduces the bug (should fail before the fix)
2. Fix the bug
3. Verify the test passes
4. Document the bug in the test docstring:

```python
class TestMyFeatureRegression:
    def test_specific_bug_description_regression(self):
        """Regression test: Brief description of the bug.

        Bug reported: What was observed.
        Root cause: Why it happened.
        Fix: What was changed.
        """
        # Test implementation...
```

## Commit Messages

Follow **Conventional Commits**:

```
<type>(<scope>): <subject>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

**Break changes into logical, atomic commits.** Separate schema changes, implementation, tests, and documentation into individual commits. Tests should pass after each commit.

## Security

**Never reference `config/config.yaml`** in code, tests, or documentation — it contains secrets and is git-ignored. Always use `config/example.yaml` or mock configs in tests.

## Adding New Data Sources

See [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) for a complete guide. The short version:

1. Create a plugin in `src/ingestion/sources/` implementing `SourcePlugin` ABC
2. Plugins are auto-discovered — no manual registration needed
3. Add comprehensive tests with mocked APIs
4. Update `config/example.yaml` with the new source's configuration

## Project Structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
├── ingestion/        # Data ingestion
│   └── sources/      # Source plugins (auto-discovered)
├── llm/              # Ollama interaction (optional)
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Scoring pipeline and engine
├── enrichment/       # Background metadata enrichment
├── conversation/     # Conversational AI chat system
├── models/           # Data models
└── utils/            # Utility functions
tests/                # Mirrors src/ structure
config/               # Configuration files
templates/            # Import file templates (CSV, JSON, Markdown)
docs/                 # Additional documentation
```

## UI Themes

The web interface supports custom themes. Each theme is a folder in `src/web/static/themes/` containing a `theme.json` metadata file and a `colors.css` file that overrides CSS color variables.

See [docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md) for a complete guide on creating community themes.

## Questions?

If you have questions about these guidelines, please open an issue.
