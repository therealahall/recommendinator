# Contributing Guidelines

## Getting started

```bash
uv sync --locked --extra ai --extra dev
python3.11 -m pytest
```

Python 3.11 is required, for ChromaDB compatibility. Branch, change, get the
checks green, open a PR.

## Local development with Docker

Layer the dev override on the production compose file for hot reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

The override builds locally instead of pulling from GHCR, bind-mounts `./src`,
`./templates` and `./pyproject.toml` so Python edits need no rebuild, and runs
uvicorn with `--reload`. The `pyproject.toml` mount keeps the runtime
`__version__` in step with python-semantic-release bumps.

Run the frontend on the host, where Vite's HMR works best:

```bash
pnpm dev
```

Vite serves on 5173 and proxies API calls to the container on 18473. The API
requires a bearer token, so the app asks once for the `web.api_token` you set in
`config/config.yaml` and keeps it in the browser. Override either end from your
shell or a gitignored root `.env`, which is what a dev server behind a reverse
proxy needs:

| Variable | Default | Effect |
|----------|---------|--------|
| `DEV_SERVER_PORT` | `5173` | Port Vite listens on. |
| `DEV_SERVER_API_TARGET` | `http://localhost:18473` | Origin `/api` and `/static/themes` proxy to. |
| `DEV_SERVER_HMR_CLIENT_PORT` | the port Vite listens on | Port the browser opens the hot-reload websocket on. Set it to the proxy's public port when that differs. |
| `DEV_SERVER_HMR_PROTOCOL` | `ws` | Websocket scheme. Set `wss` when the proxy terminates TLS. |

The HMR *host* is deliberately not configurable. Unset, the browser reuses the
hostname it loaded the page from, the only behaviour that works when one dev
server is reached under more than one name.

Your gitignored `docker-compose.override.yml` merges alongside both, so all three
compose with one command.

## Quality checks

Every one must pass before a PR:

```bash
python3.11 scripts/check_review_agents.py                     # Review agents are loadable
python3.11 -m black --check src/ tests/ scripts/ conftest.py  # Formatting
python3.11 -m ruff check src/ tests/ scripts/ conftest.py     # Linting
python3.11 -m mypy src/ scripts/ conftest.py                  # Type checking (strict)
python3.11 -m pytest                                          # All tests pass
pnpm vue-tsc --noEmit                                         # Frontend type checking
pnpm vitest run                                               # Frontend tests
```

`make check` runs exactly those, in that order. Always use `python3.11`
explicitly, never bare `python` or `python3`.

## Code standards

Black for formatting, MyPy strict for types, Ruff for linting. Google-style
docstrings, type hints on every signature. Name things fully, without
abbreviating:

```python
# Good
for item, item_embedding in zip(items, embeddings, strict=True):
    storage_manager.save_content_item(item, embedding=item_embedding)

# Bad
for i, emb in zip(items, embeddings):
    storage_manager.save_content_item(i, embedding=emb)
```

Avoid `i`, `j`, `e`, `emb`, `ct`, `cfg` and single letters. `_` for unused and
`cls` for class methods are the exceptions.

- **DRY.** Write a pattern three times and extract a helper. Search first:
  `get_enum_value()`, `extract_and_normalize_genres()` and `get_feature_flags()`
  already exist.
- **No `Any`** where a real type exists. `TYPE_CHECKING` imports break cycles
  without losing types.
- **Keyword arguments** for non-obvious parameters: `save_item(item, embedding=emb)`.
- **`if x is not None:`**, not `if x:`, when the value could be `0`, `False` or `""`.
- **Delete dead code.** No compatibility wrappers, no-op blocks, uncalled methods.
- **No defensive `or {}`** when the model field already defaults.
- **Copy dicts before mutating** one passed in from outside.
- **Module-level imports only.** No inline `import` in a function, no
  bottom-of-file import hacks.
- **Never expose internal errors** in HTTP responses. Generic message out, detail
  to the log.
- **Data-driven patterns** over copy-pasted branches differing only in names.
- **Keep developer-facing files self-contained.** Docs, tooling, CI, compose and
  build files, `tests/` and `.claude/` must not point at a path outside the
  repository. Neither a reader nor CI can verify one.
  `tests/test_repository_self_contained.py` enforces it. `src/` is exempt,
  because application code genuinely addresses the machine it runs on.
- **The guard also bans the APIs that build a home-relative path**, so a
  hostile-input test can trip on its own payload. When the outside path IS the
  subject, exempt that one line with a comment marker. The reason is required,
  and it counts only when the marker opens a comment (`#`, `//`, or `<!--`), one
  per language in scope: `#` for Python, YAML and shell, `//` for TypeScript,
  `<!--` for Markdown. Quoting it in prose exempts nothing.

  ```python
  scan_root = Path(configured).expanduser()  # self-contained: allow the API under test
  ```

  **Never water down a security test to satisfy the guard.**

## Testing

80%+ coverage. Everything new gets tests. Cross-cutting tests mirror `src/` under
`tests/`, and plugin tests live next to the plugin. Mock every external
dependency. Never make a real network request.

```bash
python3.11 -m pytest                              # All tests
python3.11 -m pytest tests/test_web_api.py -v     # One file
python3.11 -m pytest --cov=src --cov-report=html  # With coverage
```

Every bug fix gets a regression test that fails before the fix and passes after,
in a `Test<Feature>Regression` class:

```python
class TestMyFeatureRegression:
    def test_specific_bug_description_regression(self):
        """Regression test: Brief description of the bug.

        Bug reported: What was observed.
        Root cause: Why it happened.
        Fix: What was changed.
        """
```

## Pre-commit Workflow

1. Run **security-review**, **code-review**, **test-review**, **document-review**,
   **parity-review** and **accessibility-review** in parallel. An agent that
   cannot be launched is a hard stop: it reviewed nothing and said nothing, and
   unlike a skipped approval that gap is silent.
2. Triage every finding. Once a change is under review its **scope is frozen**.
   Only a correctness or security defect in code that change introduced enters
   it afterwards. Every agent labels each finding introduced or pre-existing, so
   this is a lookup, and a finding belonging to another stream is filed there,
   not fixed here and not dropped.
3. Fix what survives triage.
4. Re-run **all six**, not just the ones with findings. A fix satisfying one agent
   can break another's domain, so approval is on the final tree, not the delta.
   Repeat 2-4 until every agent approves the **same** tree.
5. Stop once the review **converges**: a round returns no criticals and no highs,
   and what is left is assertion strength or naming in code the change already got
   right. Waiting for an empty findings list is waiting forever, and every agent
   still approves the exact tree being committed. Two safeguards keep this from
   eroding: every **cut is stated explicitly with its reason**, and every
   **deferral gets a tracker issue naming the stream it lands in**.
   A deferral with no tracker entry is a cut pretending otherwise.
6. Run **commit-hygiene** to plan the atomic commit split.
7. Run `command make check`.
8. Commit to the plan. If staging triggers a formatter or any other edit, restart
   at step 4. The agents must approve the tree that gets committed.
9. Run **commit-hygiene** again before pushing.

### The review agents

Seven agents, all committed under `.claude/agents/`, so a plain clone has the
whole gate.

| Agent | Covers |
|-------|--------|
| **code-review** | Design, naming, DRY, project standards |
| **security-review** | Credential leaks, injection, unsafe patterns ([docs/SECURITY.md](docs/SECURITY.md)) |
| **test-review** | Coverage, mock hygiene, regression format, edge cases |
| **document-review** | Accuracy, completeness, cross-document consistency |
| **accessibility-review** | WCAG 2.1 AA. Self-gates on frontend file presence |
| **commit-hygiene** | Atomic commits, conventional format |
| **parity-review** | CLI/web parity, native to this repository |

Every agent but `parity-review` is shared across repositories, maintained
elsewhere, and picks up this project's rules by reading `CLAUDE.md` and `docs/`.
To improve one, edit the committed copy and say so in the PR.

Claude Code discovers `.claude/agents/` only for the directory a session started
in, so start it **at the repository root**. A subdirectory does not count.
`make check` verifies every mandated agent is committed and loadable.

## Commit messages

Conventional Commits: `<type>(<scope>): <subject>`, type one of `feat`, `fix`,
`docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`.

Commit atomically, separating schema, implementation, tests and documentation.
Tests should pass after each.

**Commit types drive version bumps**, so the wrong one ships the wrong version:

| Commit pattern | Version bump |
|----------------|-------------|
| `feat(...):`   | Minor (0.1.0 → 0.2.0) |
| `fix(...):`    | Patch (0.1.0 → 0.1.1) |
| `perf(...):`   | Patch (0.1.0 → 0.1.1) |
| `BREAKING CHANGE:` in footer | Major (0.1.0 → 1.0.0)* |
| `docs`, `style`, `refactor`, `test`, `chore`, `ci` | No bump |

*Pre-1.0, `major_on_zero = false`, so a breaking change bumps minor.

## Versioning

python-semantic-release owns the version. Never edit it by hand.

- **Source of truth**: `pyproject.toml` `[project] version`.
- **Runtime**: `src/__init__.py` prefers an adjacent `pyproject.toml` (dev and
  Docker source layouts), falling back to
  `importlib.metadata.version("recommendinator")` for wheel installs. That
  preference keeps editable installs and dev containers in step with a bump
  without a reinstall. Never hardcode a version.
- **CHANGELOG.md**: generated from commit messages. Manual edits are overwritten
  on the next release.
- **Release**: a workflow on push to `main` analyzes commits, bumps the version,
  updates the changelog, and creates the version commit and tag. A follow-up step
  regenerates `uv.lock` and commits it separately.

## Security

**Never reference `config/config.yaml`** in code, tests or documentation. It
holds secrets and is git-ignored. Use `config/example.yaml` or a mock config.

Every `/api` route requires the bearer token from `web.api_token`, applied to
the routers so a new endpoint is authenticated by being registered. Tests reach
the API through `tests.factories.authenticated_client`; a bare `TestClient` gets
401s.

## Adding a data source

Full guide: [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md). The short
version:

1. Create `src/ingestion/sources/<name>/` holding `<name>.py` (the `SourcePlugin`
   subclass), `__init__.py` (a one-line re-export), `README.md` and
   `test_<name>.py` with mocked APIs.
2. Discovery is automatic. Nothing to register.
3. Document it in `docs/DATA_SOURCES.md` and the plugin's own `README.md`. Do
   **not** add it to `config/example.yaml`, which is bootstrap-only. The
   Add-source modal and the `source` CLI build their forms from
   `get_config_schema()`, so a correct schema is all a new plugin needs.

## Project structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
│   └── static/themes/  # UI themes (folder-per-theme, auto-discovered)
├── ingestion/        # Data ingestion
│   └── sources/      # Source plugins (<name>/<name>.py + README.md + test_<name>.py)
├── llm/              # Ollama interaction (optional)
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Scoring pipeline and engine
├── enrichment/       # Background metadata enrichment
│   └── providers/    # Enrichment providers (same layout as sources)
├── conversation/     # Conversational AI chat system
├── models/           # Data models
└── utils/            # Utility functions
tests/                # Cross-cutting tests. Plugin tests live next to the plugin.
conftest.py           # Three autouse fixtures for every test in every tree: real
                      # logs and credentials isolated, timezone pinned to UTC
scripts/              # Developer tooling (check_review_agents.py)
config/               # Configuration files
templates/            # Import file templates (CSV, JSON, Markdown)
docs/                 # Additional documentation
.claude/agents/       # All seven mandated review agents
```

`src/ingestion/sources/_isolation/` is not a plugin. It holds the test proving
plugin-local tests get the root conftest's isolation, and its leading underscore
keeps the registry from importing it.

## UI themes

Each theme is a folder in `src/web/static/themes/` holding `theme.json` and a
`colors.css` that overrides CSS color variables. See
[docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md).

## Questions?

Open an issue.
