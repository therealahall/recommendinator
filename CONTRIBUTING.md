# Contributing Guidelines

Thank you for your interest in contributing to Recommendinator! This document covers everything you need to get started.

## Getting Started

1. **Fork and clone** the repository
2. **Install Python 3.11** (required for ChromaDB compatibility)
3. **Install dependencies:**
   ```bash
   uv sync --locked --extra ai --extra dev
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

### Local development with Docker (hot reload)

If you'd rather not maintain a local Python + Node toolchain, you can run the
full stack in containers with hot reload by layering `docker-compose.dev.yml`
on top of the production compose file:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

What the override does:
- **Builds locally** instead of pulling from GHCR, so changes to `Dockerfile`,
  `pyproject.toml`, or the frontend build are reflected.
- **Bind-mounts `./src`, `./templates`, and `./pyproject.toml`** into the container
  so Python edits are visible immediately without a rebuild. The `pyproject.toml`
  mount keeps the runtime `__version__` in sync with `python-semantic-release`
  bumps without rebuilding the image.
- **Starts uvicorn with `--reload`**, watching `src/` and `templates/` — backend
  changes restart in ~1 second.

Frontend hot reload runs on the host (Vite's HMR works best natively):

```bash
pnpm dev
```

Vite serves on port 5173 and proxies API calls to the container on port 18473.
Both ends are overridable from your shell or from a gitignored `.env` at the
repository root, which is what you need if the dev server sits behind a reverse
proxy:

| Variable | Default | Effect |
|----------|---------|--------|
| `DEV_SERVER_PORT` | `5173` | Port Vite listens on. |
| `DEV_SERVER_API_TARGET` | `http://localhost:18473` | Origin that `/api` and `/static/themes` are proxied to. |
| `DEV_SERVER_HMR_CLIENT_PORT` | (unset — the port Vite listens on) | Port the browser opens the hot-reload websocket on. Set it to the proxy's public port when that differs from `DEV_SERVER_PORT`. |
| `DEV_SERVER_HMR_PROTOCOL` | (unset — `ws`) | Scheme for the hot-reload websocket. Set it to `wss` when the proxy terminates TLS. |

The HMR *host* is deliberately not configurable. Left unset, the browser reuses
the hostname it loaded the page from, which is the only behaviour that works when
the same dev server is reached under more than one name.

Your gitignored `docker-compose.override.yml` (for personal mounts like private
plugin directories) merges automatically alongside both files, so all three
compose cleanly with one command.

**Automated code review:** When using Claude Code, seven review agents run before commits:
- **code-review** — Reviews code quality, design, naming, DRY compliance, and adherence to project standards.
- **security-review** — Audits for vulnerabilities, credential leaks, and unsafe patterns. See [docs/SECURITY.md](docs/SECURITY.md) for details.
- **test-review** — Audits test coverage, correctness, mock hygiene, regression test format, and edge case handling.
- **document-review** — Verifies documentation accuracy, completeness, and cross-document consistency.
- **accessibility-review** — Verifies WCAG 2.1 AA compliance for frontend components (semantic HTML, ARIA attributes, keyboard navigation, focus management, color contrast). Self-gates on frontend file presence.
- **commit-hygiene** — Enforces atomic commit structure and conventional commit format.
- **parity-review** — Enforces CLI/web feature parity (native to this repository).

All seven agents must approve changes before they are committed, and all seven are checked in under `.claude/agents/`, so a plain clone has the whole gate. Six of them are project-agnostic and shared across repositories; their canonical source is maintained outside this repository, and they pick up this project's rules by reading `CLAUDE.md` and `docs/`. To improve one of them, edit the committed copy and say so in the PR. `parity-review` is native here, because CLI/web parity is this repository's own invariant.

Claude Code only discovers `.claude/agents/` for the directory a session started in, so start it **at the repository root** — a subdirectory does not count. `make check` verifies that every mandated agent is committed and loadable; an agent that cannot be launched is a hard stop, because it reviewed nothing and said nothing. Contributors can expect feedback on PRs touching security-sensitive areas (authentication, configuration, network requests) as well as general code quality, test coverage, and documentation accuracy concerns.

## Quality Checks

**Every check must pass before submitting a PR:**

```bash
python3.11 scripts/check_review_agents.py          # Review agents are loadable
python3.11 -m black --check src/ tests/ scripts/   # Formatting
python3.11 -m ruff check src/ tests/ scripts/      # Linting
python3.11 -m mypy src/ scripts/                   # Type checking (strict)
python3.11 -m pytest                               # All tests pass
pnpm vue-tsc --noEmit                              # Frontend type checking
pnpm vitest run                                    # Frontend tests
```

Or use the Makefile: `make check` runs exactly those, in that order.

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
- **Keep developer-facing files self-contained** — docs, tooling, CI, compose and build files, `tests/`, and `.claude/` files must not point at a path outside the repository (a home directory, one machine's layout). A reader with no context cannot verify one, and neither can CI. `tests/test_repository_self_contained.py` enforces this. `src/` is exempt, because application code genuinely addresses the machine it runs on.
- **The self-containment guard also bans the APIs that build a home-relative path**, so a legitimate hostile-input test can trip on its own payload. When the outside path IS what a test is about, exempt that single line with a comment marker: the reason is required, and it only counts when the marker opens a comment (`#`, `//`, or `<!--`) — one per language in scope: `#` for Python, YAML and shell, `//` for TypeScript, `<!--` for Markdown — so quoting it in prose exempts nothing.

  ```python
  scan_root = Path(configured).expanduser()  # self-contained: allow the API under test
  ```

  **Never water down a security test to satisfy the guard.**
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

## Pre-commit Workflow

Before committing, run the review agents and quality checks:

1. Run **security-review**, **code-review**, **test-review**, **document-review**, **parity-review**, and **accessibility-review** agents (can run in parallel). An agent that cannot be launched is a hard stop, not a step to skip — it reviewed nothing, and unlike a skipped approval that gap is silent. All seven agents are in your checkout, so they work on any machine; start Claude Code at the repository root or none of them resolve
2. Triage every finding against the **frozen scope**. Once a change is under review its scope is frozen: nothing new enters it unless it is a correctness or security defect in code that change introduced. Every agent labels each finding as introduced by the diff or pre-existing, so this is a lookup rather than a judgement call. A real finding belonging to another stream is filed against that stream — not fixed here, and not dropped
3. Address the findings that survive triage
4. Re-run **all six agents from step 1** — not just the ones that had findings. A fix that satisfies one agent can break another's domain, so approval is on the final tree, not on the delta. Repeat steps 2–4 until every agent approves the **same** tree
5. Stop once the review **converges**. When a round returns no criticals and no highs, and what remains is assertion strength, naming, or tidiness in code the change already got right, that is the end of the loop: file what is relevant, cut what is not, say which is which, and commit. A round whose only output is a shorter list of smaller nits is a round that should not have run — good reviewers converge on quality, never on silence, so waiting for an empty findings list is waiting forever. This changes what counts as *resolved*; it does not make the gate optional, and every agent still approves the exact tree being committed, per step 4. Two safeguards keep the rule from becoming erosion: **every cut is stated explicitly with its reason**, and **every deferral gets a tracker issue naming the stream it lands in**. A deferral with no tracker entry is a cut pretending otherwise
6. Run **commit-hygiene** agent to plan atomic commit split
7. Run all quality checks: `command make check`
8. Commit following the split plan. If staging triggers a formatter or any other code edit, restart at step 4 — the agents must approve the exact tree that gets committed
9. Run **commit-hygiene** again before pushing to verify commit structure

## Commit Messages

Follow **Conventional Commits**:

```
<type>(<scope>): <subject>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`

**Break changes into logical, atomic commits.** Separate schema changes, implementation, tests, and documentation into individual commits. Tests should pass after each commit.

**Commit types drive automatic version bumps.** This project uses [python-semantic-release](https://python-semantic-release.readthedocs.io/) to parse commit messages and determine version numbers. Using the wrong commit type doesn't just hurt readability — it causes incorrect version numbers:

| Commit pattern | Version bump |
|----------------|-------------|
| `feat(...):`   | Minor (0.1.0 → 0.2.0) |
| `fix(...):`    | Patch (0.1.0 → 0.1.1) |
| `perf(...):`   | Patch (0.1.0 → 0.1.1) |
| `BREAKING CHANGE:` in footer | Major (0.1.0 → 1.0.0)* |
| `docs`, `style`, `refactor`, `test`, `chore`, `ci` | No bump |

*While the project is pre-1.0, `major_on_zero = false` — breaking changes bump minor instead of major.

## Versioning & Releases

The project uses **automatic semantic versioning**:

- **Version source of truth**: `pyproject.toml` `[project] version` field, written by python-semantic-release
- **Runtime version**: `src/__init__.py` resolves the version by preferring an adjacent `pyproject.toml` (dev and Docker source layouts) and falling back to `importlib.metadata.version("recommendinator")` for wheel installs — never hardcode versions. The pyproject.toml preference keeps editable installs and Docker dev containers in sync after `python-semantic-release` bumps the version without requiring a reinstall.
- **CHANGELOG.md**: Auto-generated from commit messages — **do not edit manually** (edits will be overwritten on the next release)
- **Release workflow**: A GitHub Actions workflow on push to `main` analyzes commits, bumps the version, updates CHANGELOG.md, and creates a version commit and tag. A follow-up step regenerates `uv.lock` and commits it separately
- **No manual version bumps**: Never edit the version in `pyproject.toml` by hand — let the release workflow handle it

## Security

**Never reference `config/config.yaml`** in code, tests, or documentation — it contains secrets and is git-ignored. Always use `config/example.yaml` or mock configs in tests.

## Adding New Data Sources

See [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) for a complete guide. The short version:

1. Create a plugin folder `src/ingestion/sources/<name>/` containing `<name>.py` (the `SourcePlugin` subclass), `__init__.py` (one-line re-export), `README.md`, and `test_<name>.py`
2. Plugins are auto-discovered — no manual registration needed
3. Tests live alongside the implementation, with mocked APIs
4. Document the source in `docs/DATA_SOURCES.md` and the plugin's own `README.md` — do **not** add it to `config/example.yaml`, which is bootstrap-only. The Add-source modal and the `source` CLI build their forms from `get_config_schema()`, so a correct schema is all a new plugin needs to be configurable.

## Project Structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
│   └── static/themes/  # UI themes (folder-per-theme, auto-discovered)
├── ingestion/        # Data ingestion
│   └── sources/      # Source plugins (folder-per-plugin: <name>/<name>.py + README.md + test_<name>.py)
├── llm/              # Ollama interaction (optional)
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Scoring pipeline and engine
├── enrichment/       # Background metadata enrichment
│   └── providers/    # Enrichment providers (folder-per-provider, same layout as sources)
├── conversation/     # Conversational AI chat system
├── models/           # Data models
└── utils/            # Utility functions
tests/                # Cross-cutting tests (CLI, web, storage, recommendations, conversation).
                      # Plugin-local tests live next to the plugin: src/.../<plugin>/test_<plugin>.py.
scripts/              # Developer tooling (check_review_agents.py)
config/               # Configuration files
templates/            # Import file templates (CSV, JSON, Markdown)
docs/                 # Additional documentation
.claude/agents/       # All seven mandated review agents (six shared, vendored; parity-review native)
```

## UI Themes

The web interface supports custom themes. Each theme is a folder in `src/web/static/themes/` containing a `theme.json` metadata file and a `colors.css` file that overrides CSS color variables.

See [docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md) for a complete guide on creating community themes.

## Questions?

If you have questions about these guidelines, please open an issue.
