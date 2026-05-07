# Development Patterns

Operational playbook for working in this repo. CLAUDE.md links here from its workflow sections — read this file at the start of any feature, bug fix, or plan-mode task.

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
bd create --title="Add parser for new source" --description="..." --type=task --priority=2
bd create --title="Write tests for new parser" --description="..." --type=task --priority=2
bd create --title="Update CLI to support new source" --description="..." --type=task --priority=2
bd create --title="Update documentation" --description="..." --type=chore --priority=2

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
8. Atomic commits with proper message format following conventional commit standards

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

## Anti-Churn Checklist (run BEFORE writing code)

Review agents repeatedly flag the same classes of issues. Reading this list before each task — and actively searching for the relevant patterns in the codebase — avoids the same fixes round after round.

### Before writing ANY code

1. **Grep for a sibling that already does the thing.** New CLI subcommand? Read an existing one in `src/cli/commands.py` end-to-end first. New API endpoint? Read a sibling in `src/web/api.py`. New test file? Read the nearest neighbor in `tests/` first. Match its structure exactly — imports order, helper usage, naming, error handling, output format branching.
2. **Search `src/utils/` and module-local helpers before creating a new helper.** If two sites would use it, it already exists or needs to live there. Examples: `extract_tv_season_fields` (item serialization), `_item_to_dict` (CLI item output), `get_feature_flags` (AI flag checks), `list_merge`, `series`, `sorting`.
3. **For CLI changes, diff against the web API first.** CLI and web UI are mirrors. Before adding a CLI flag/field, read the equivalent endpoint's Pydantic response model and request params. The JSON shape MUST match exactly — same keys, same types, same empty-result behavior.
4. **For test changes, read `tests/<area>/conftest.py` first.** If a shared fixture or helper exists (`cli_runner`, `_invoke_with_mocks`, `_cli_patches`), use it. Never redefine a fixture locally that already lives in conftest.

### Imports (code-review flags these every time)

- **All imports at module top.** No function-level imports, no `import x as _x` inside a test method. If you catch yourself writing `import json` inside a function, stop and move it to the top of the file.
- **Don't reorder imports manually** — let ruff/isort handle it. If ruff splits a block you want kept together (aliased auth imports, etc.), the existing fix is `# noqa: I001` on the `from __future__ import annotations` line.

### Tests (test-review flags these every time)

- **Use `spec=RealClass` on MagicMock** for any real type (StorageManager, RecommendationEngine, ContentItem, etc.). A bare `MagicMock()` is almost always wrong.
- **Use the shared `_invoke_with_mocks` / `_cli_patches` helpers** from `tests/cli/conftest.py`. Do NOT write nested `with patch(): with patch(): with patch():` pyramids — if you see yourself nesting patches more than two deep, you're duplicating what conftest provides.
- **`ContentItem` construction requires `status`.** Most tests also need `rating` depending on the path. When a test fails with "Missing named argument", check the dataclass signature, don't guess.
- **Regression tests go in a `Test<Feature>Regression` class with a docstring** documenting: the bug symptom, root cause, and what the fix does. No bare tests with cryptic names.
- **Assertions must be strong.** `assert result.exit_code == 0` is necessary but not sufficient — also assert specific output strings, mock call args, or parsed JSON keys. "Output is non-empty" is not a real assertion.

### CLI/web parity (parity-review blocks on ANY drift)

- **JSON output on empty results is `[]` or the empty response object — never a text message.** Text messages only go to the human-readable format. Check every `if not items: click.echo("No ...")` — it needs an `if output_format == "json"` branch that emits valid empty JSON first.
- **JSON field set must match the web Pydantic response exactly.** Before committing a CLI JSON path, open the matching `*Response` model in `src/web/api.py` and diff the key sets. Missing/extra fields = blocking drift.
- **Every CLI command needs a `--format json` option if the web API returns JSON.** Every web API flag/param needs a matching CLI option. No exceptions.
- **Serialization goes through shared helpers** (`_item_to_dict`, `extract_tv_season_fields`). Do not hand-roll dict construction in both CLI and API.

### Code style (code-review flags these every time)

- **No backwards-compat shims, no "removed" comments, no unused `_var` renames.** Delete the code outright.
- **Default to no comments.** Only comment the WHY of a non-obvious constraint — never the WHAT, never "added for task X", never reference the current review round.
- **No try/except that re-raises unchanged.** No validation for "can't happen" internal states. Trust internal code; only validate at system boundaries.
- **No feature flags or config toggles unless the user asked for them.** Just change the code.
- **Click `IntRange` for bounded ints, Click choices for enums.** Don't manually validate `if count > max: abort()` for simple bounds — but DO validate against config-driven limits (e.g., `max_count` from config) since those aren't Click-expressible.

### Shell discipline (user preferences, memory-backed)

- **One Bash call per logical step.** Never chain with `&&`/`;`. See `feedback_dont_chain_commands.md`.
- **Run `python3.11 -m black src/ tests/` and `python3.11 -m ruff check src/ tests/ --fix` BEFORE `command make check`**, so make check doesn't fail on auto-fixable formatting. See `feedback_always_format_before_check.md`.
- **Do not read raw subagent output files** to verify agent results — the agent returns its finding in its tool result. See `feedback_no_grep_agent_output.md`.
- **Never wait/poll on background agents.** The runtime notifies on completion.

### If an agent flags something, fix the CLASS, not the instance

When a review agent flags an issue, search the rest of the diff for the same pattern and fix all occurrences in one pass. Don't wait for round N+1 to discover the same issue elsewhere. After fixing, grep the whole diff for the pattern to confirm it's gone.
