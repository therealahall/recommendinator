# Contributing Guidelines

## Getting started

```bash
uv sync --locked --extra dev
python3.11 -m pytest
```

Python 3.11 is required: `requires-python` refuses every other minor. Branch,
change, get the checks green, open a PR.

## Local development with Docker

Copy the dev override into place once, then bring the stack up for hot reload:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up
```

The override builds locally instead of pulling from GHCR, bind-mounts `./src`,
`./templates` and `./pyproject.toml` so Python edits need no rebuild, and runs
uvicorn with `--reload`. The `pyproject.toml` mount keeps the runtime
`__version__` in step with python-semantic-release bumps.

Run the frontend on the host, where Vite's HMR works best:

```bash
pnpm dev
```

Vite serves on 5173 and proxies API calls to the container on 18473, so signing
in through it sets the session cookie exactly as the container would. Override
either end from your shell or a gitignored root `.env`, which is what a dev
server behind a reverse proxy needs:

| Variable | Default | Effect |
|----------|---------|--------|
| `DEV_SERVER_PORT` | `5173` | Port Vite listens on. |
| `DEV_SERVER_API_TARGET` | `http://localhost:18473` | Origin `/api` and `/static/themes` proxy to. |
| `DEV_SERVER_HMR_CLIENT_PORT` | the port Vite listens on | Port the browser opens the hot-reload websocket on. Set it to the proxy's public port when that differs. |
| `DEV_SERVER_HMR_PROTOCOL` | `ws` | Websocket scheme. Set `wss` when the proxy terminates TLS. |

The HMR *host* is deliberately not configurable. Unset, the browser reuses the
hostname it loaded the page from, the only behaviour that works when one dev
server is reached under more than one name.

`docker-compose.override.yml` is gitignored, so your own mounts belong in it too.
Compose only picks it up when the command names no file, so run it without `-f`
or those mounts disappear with nothing said.

## Quality checks

One command, and it must pass before a PR:

```bash
make check
```

It runs Black, Ruff, MyPy, pytest, the same four over `private/` when you have
one, then `vue-tsc` and Vitest, installing the frontend dependencies first when
`node_modules` is missing — a fresh clone or worktree needs no separate `pnpm
install`. When you run one of those tools directly, spell the interpreter
`python3.11`, never bare `python` or `python3`.

CI runs the same command and reports it as one status, `check / check`. That
name is new — the gate moved into a reusable workflow — so branch protection
has to be repointed at it in the repository settings. Until a maintainer does
that, every pull request waits on a status nothing reports.

## Code standards

Black for formatting, MyPy strict for types, Ruff for linting. Google-style
docstrings, type hints on every signature. Name things fully, without
abbreviating:

```python
# Good
for item, item_user_id in zip(items, user_ids, strict=True):
    storage_manager.save_content_item(item, user_id=item_user_id)

# Bad
for i, u in zip(items, user_ids):
    storage_manager.save_content_item(i, user_id=u)
```

Avoid `i`, `j`, `e`, `ct`, `cfg` and single letters. `_` for unused and `cls`
for class methods are the exceptions.

- **DRY.** Write a pattern three times and extract a helper. Search first:
  `get_enum_value()`, `extract_and_normalize_genres()` and `get_sort_title()`
  already exist.
- **No `Any`** where a real type exists. `TYPE_CHECKING` imports break cycles
  without losing types.
- **Keyword arguments** for non-obvious parameters: `save_item(item, user_id=1)`.
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
  repository. Neither a reader nor CI can verify one. `src/` is exempt,
  because application code genuinely addresses the machine it runs on.

## Testing

Everything new gets tests. Cross-cutting tests mirror `src/` under `tests/`, and
plugin tests live next to the plugin. Mock every external dependency. Never make
a real network request.

```bash
python3.11 -m pytest                              # All tests
python3.11 -m pytest tests/test_web_api.py -v     # One file
python3.11 -m pytest --cov=src --cov-report=html  # With coverage
```

Every bug fix gets a regression test that fails before the fix and passes after.
Name it for the bug it catches; the name is the documentation.

```python
def test_blank_review_no_longer_erases_a_written_one(self):
    ...
```

## Pre-commit Workflow

1. Run **parity-review** and whatever review agents your environment provides,
   in parallel. An agent that cannot be launched is a hard stop: it reviewed
   nothing and said nothing, and unlike a skipped approval that gap is silent.
2. Triage every finding. Once a change is under review its **scope is frozen**.
   Only a correctness or security defect in code that change introduced enters
   it afterwards. Every agent labels each finding introduced or pre-existing, so
   this is a lookup, and a finding belonging to another stream is filed there,
   not fixed here and not dropped.
3. Fix what survives triage.
4. Re-run **every agent from step 1**, not just the ones with findings. A fix satisfying one agent
   can break another's domain, so approval is on the final tree, not the delta.
   Repeat 2-4 until every agent approves the **same** tree.
5. Stop once the review **converges**: a round returns no criticals and no highs,
   and what is left is assertion strength or naming in code the change already got
   right. Waiting for an empty findings list is waiting forever, and every agent
   still approves the exact tree being committed. Two safeguards keep this from
   eroding: every **cut is stated explicitly with its reason**, and every
   **deferral gets a tracker issue naming the stream it lands in**.
   A deferral with no tracker entry is a cut pretending otherwise.
6. Plan the atomic commit split before staging anything.
7. Run `command make check`.
8. Commit to the plan. If staging triggers a formatter or any other edit, restart
   at step 4. The agents must approve the tree that gets committed.
9. Verify commit structure and messages before pushing.

### The review agents

One agent ships with the repository: **parity-review**
(`.claude/agents/parity-review.md`) — CLI/web feature parity. Run it on any
change to the capability surface; it approves immediately otherwise. `make
check` and human review cover everything else.

Claude Code discovers `.claude/agents/` only for the directory a session started
in, so start it **at the repository root**. A subdirectory does not count.

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
- **Release**: once CI passes on `main`, a workflow analyzes the commits, bumps
  the version, updates the changelog and regenerates `uv.lock`, then puts all
  three in one version commit and tags it.

## Security

**Never reference `config/config.yaml`** in code, tests or documentation. It
holds secrets and is git-ignored. Use `config/example.yaml` or a mock config.

Every `/api` route but the four under `/api/auth` requires a session cookie,
applied to the routers so a new endpoint is authenticated by being registered.
Tests reach the API through `tests.factories.authenticated_client`; a bare
`TestClient` gets 401s.

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
├── cli/              # Click commands: commands/ holds one module per group,
│                     # _shared.py the helpers more than one of them uses
├── web/              # FastAPI app: routing, guards, app state
│   └── static/themes/  # UI themes (folder-per-theme, auto-discovered)
├── config/           # config.yaml loading + component factories
├── sources/          # service.py: configured-source CRUD
├── auth/             # GOG/Epic/Trakt OAuth flows
├── ingestion/        # Data ingestion
│   ├── importers/    # One-off file formats
│   └── sources/      # Source plugins (<name>/<name>.py + README.md + test_<name>.py)
├── storage/          # SQLite
├── recommendations/  # Scoring pipeline and engine
├── enrichment/       # Background metadata enrichment
│   └── providers/    # Enrichment providers (same layout as sources)
├── models/           # Data models
└── utils/            # Utility functions
tests/                # Cross-cutting tests. Plugin tests live next to the plugin.
conftest.py           # Five autouse fixtures for every test in every tree: real
                      # logs and credentials isolated, timezone pinned to UTC,
                      # reads confined to tmp_path, network limited to loopback
config/               # Configuration files
templates/            # Blank import templates
docs/                 # Additional documentation
.claude/agents/       # The native parity-review agent
```

`src/ingestion/sources/_isolation/` is not a plugin. It holds the test proving
plugin-local tests get the conftest's isolation; its underscore keeps the
registry out.

## UI themes

Each theme is a folder in `src/web/static/themes/` holding `theme.json` and a
`colors.css` that overrides CSS color variables. See
[docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md).

## Questions?

Open an issue.
