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

## After Conversation Compaction

**CRITICAL: When the conversation is compacted (context compressed), you MUST immediately:**

1. **Re-read this file**: `CLAUDE.md` — compaction loses significant context, and these instructions must be fully reloaded.
2. **State what you were working on**: Summarize the current task, what has been completed so far, and what remains.
3. **Ask clarifying questions**: Confirm with the user that your understanding is correct before continuing. Ask about any details you are uncertain about — it is better to re-confirm than to proceed with stale or incorrect assumptions.

Do not silently continue working after compaction. Always pause, re-orient, and verify before resuming.

## Project Structure

```
src/
├── cli/              # Click CLI interface
├── web/              # FastAPI web interface
│   └── static/themes/  # UI themes (folder-per-theme, auto-discovered)
├── ingestion/        # Data ingestion
│   └── sources/      # Source plugins (auto-discovered)
├── llm/              # Ollama interaction
├── storage/          # SQLite + ChromaDB
├── recommendations/  # Recommendation engine (scorers, pipeline, ranking, genre_clusters)
├── enrichment/       # Background metadata enrichment
├── conversation/     # Conversational AI chat system
├── models/           # Data models (ContentItem, ContentType, UserPreferenceConfig)
└── utils/            # Utility functions (list_merge, series, sorting)
tests/                # Mirrors src/ structure
config/               # Configuration files (example.yaml for tests)
private/              # Gitignored — private plugins NOT in the open source repo
└── plugins/          # Private source plugins (personal_site_games.py, etc.)
```

## Private Plugins (gitignored)

**IMPORTANT:** The `private/` directory is in `.gitignore` and invisible to Glob/Grep tools (which respect gitignore). Always check it explicitly with `Read` or `ls` when investigating plugin issues.

- **`private/plugins/personal_site_games.py`** — Imports video games from the owner's personal blog (markdown files with YAML frontmatter). Reads from `~/Programming/ahall/personal-site/src/content/games/`.
- Private plugins follow the same `SourcePlugin` interface as `src/ingestion/sources/` plugins.
- Tests for private plugins live alongside them or in a local test runner — they are NOT in the `tests/` directory.

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

**Tests are NEVER skipped.** Do not use `--ignore`, `pytest.mark.skip`, `@pytest.skip`, or any other mechanism to skip or exclude tests. Every test in the suite must run and pass. If a test is slow, hanging, or broken, **fix the test** — do not work around it by skipping it.

**Run all four checks after every material change.** Each source code change must leave all four quality checks passing before moving on to the next change. Documentation-only changes (README, CLAUDE.md, docs/) do not require re-running tests.

```bash
command make check
```

Note: Use `command make check` (not bare `make check`) to bypass a zsh shell snapshot function that shadows the `make` binary in Claude Code's environment.

### Agent-Enforced Standards

Code quality rules (naming, DRY, type safety, dead code, imports, mutation), security rules, test standards, regression test format, commit conventions, and documentation completeness are enforced by the subagents. The main agent's job is to write clean code, follow the workflows below, and run the agents before committing. See the agent files for the full rule sets:

- **code-review** — naming, DRY, type safety, dead code, imports, mutation, over/under-engineering
- **security-review** — credential exposure, injection, CORS, error disclosure, dependencies
- **test-review** — coverage, mock hygiene, regression test format, edge cases, performance
- **commit-hygiene** — atomic commits, conventional format, message quality, documentation gaps

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

## Claude Code Tooling

The project uses Claude Code plugins and custom agents to maintain code quality and security. Configuration lives in `.claude/settings.json` (plugins) and `.claude/agents/` (agent definitions).

### Enabled Plugins

- **Pyright LSP** (`pyright-lsp@claude-plugins-official`) — Real-time type checking and diagnostics via LSP.
- **Frontend Design** (`frontend-design@claude-plugins-official`) — Production-grade UI components for the FastAPI web interface.

### Custom Agents

- **security-review** — Pre-commit security audit. See `.claude/agents/security-review.md`.
- **code-review** — Pre-commit code quality review. See `.claude/agents/code-review.md`.
- **test-review** — Pre-commit test coverage and quality audit. See `.claude/agents/test-review.md`.
- **commit-hygiene** — Atomic commit structure and conventional format. See `.claude/agents/commit-hygiene.md`.

**All agents must approve changes before marking tasks as complete.** Run security-review, code-review, and test-review before `command make check`. Run commit-hygiene before committing (to plan the split) and before pushing (to verify commit structure).

## Architecture Principles

1. **Separation of Concerns**: Keep ingestion, LLM, storage separate
2. **Testability**: Design for easy mocking
3. **Extensibility**: Easy to add new data sources/content types
4. **Configuration**: No hardcoded values
5. **Error Handling**: Graceful with clear messages

## Plan Mode Workflow

**When plan mode is used and the plan is approved, create beads issues for each step.**

After `ExitPlanMode` is approved by the user:

1. **Create a bead for each plan step** using `bd create`, with:
   - `--title` matching the plan step summary
   - `--description` including the detailed implementation notes from the plan
   - `--type` appropriate to the step (`task`, `feature`, `chore`, etc.)
   - `--priority=2` unless the step is clearly critical (0) or low-priority (4)
2. **Set up dependencies** between steps using `bd dep add` where steps must be completed in order (e.g., implementation blocks tests, tests block docs)
3. **Work through the beads in order** — mark each `in_progress` before starting, `close` when done
4. **Use parallel `bd create` calls** when creating multiple beads to maximize efficiency

Example flow after plan approval:
```bash
# Create beads for each plan step (parallel)
bd create --title="Add parser for new source" --description="..." --type=task --priority=2
bd create --title="Write tests for new parser" --description="..." --type=task --priority=2
bd create --title="Update CLI to support new source" --description="..." --type=task --priority=2
bd create --title="Update documentation" --description="..." --type=chore --priority=2

# Set dependencies (tests depend on implementation, docs depend on CLI)
bd dep add <tests-id> <parser-id>
bd dep add <cli-id> <parser-id>
bd dep add <docs-id> <cli-id>
```

This ensures every plan step is tracked, has clear acceptance criteria, and nothing is forgotten across context compaction or long sessions.

## Adding New Features

**Think before acting.** Do not jump straight into writing code. Ask clarifying questions if requirements are ambiguous, there are multiple valid approaches, or the scope is unclear.

1. Read relevant documentation
2. Search the codebase for existing patterns — match conventions you find
3. Ask questions if anything is unclear or if there are trade-offs to decide
4. Write tests first (TDD recommended)
5. Implement following existing patterns
6. Ensure all checks pass (`command make check`)
7. Update documentation (ARCHITECTURE.md, README.md, QUICKSTART.md, CLAUDE.md, relevant docs/ files)
8. Commit with proper message format

## Adding New Data Sources

1. Create parser in `src/ingestion/sources/`
2. Follow existing patterns (goodreads.py, steam.py)
3. Yield `ContentItem` objects
4. Add comprehensive tests with mocked APIs
5. Update CLI/web to support new source
6. Update docs: ARCHITECTURE.md, README.md, config/example.yaml, docs/PLUGIN_DEVELOPMENT.md

## Bug Fixes

1. Write regression test first (fails before fix, passes after)
2. Fix the bug
3. Document bug in test docstring (what was reported, root cause, fix applied)
4. Update docs if the fix changes behavior or corrects something documented incorrectly
5. Commit with `fix` type

## Pre-commit Workflow

1. Run **security-review**, **code-review**, and **test-review** agents
2. Run **commit-hygiene** agent to plan commit split
3. Run `command make check` (pytest, black, mypy, ruff)
4. Commit following the split plan from commit-hygiene
5. Run **commit-hygiene** again pre-push to verify commit structure
