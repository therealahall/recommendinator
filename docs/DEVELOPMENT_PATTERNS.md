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
2. Follow existing patterns (goodreads_csv.py, steam.py)
3. Yield `ContentItem` objects
4. Add comprehensive tests with mocked APIs
5. Update CLI/web to support new source
6. Update docs: ARCHITECTURE.md, README.md, docs/DATA_SOURCES.md, docs/PLUGIN_DEVELOPMENT.md (NOT config/example.yaml — it is bootstrap-only; sources are configured from the Data tab / `source` CLI off the plugin's `get_config_schema()`)

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

## Frontend conventions (Vue 3 / TypeScript)

The Vue 3 + Tailwind CSS v4 frontend lives in `resources/js/` and `resources/css/`. The general code-quality rules (DRY, naming, dead code, type safety, simplicity, over/under-engineering) apply to TypeScript and Vue equally — no exceptions because "it's frontend." The code-review and accessibility-review agents enforce the rules below; they are project-specific, so they live here rather than in those (project-agnostic) agents.

### Vue-Specific Concerns

- **Component organization follows strict Atomic Design** (Brad Frost's methodology). This is not optional — it is how the component hierarchy is structured and enforced:

  **Directory structure:**
  ```
  components/
  ├── atoms/        # Smallest indivisible UI elements
  ├── molecules/    # Simple groups of atoms working together
  ├── organisms/    # Complex, distinct UI sections
  ├── templates/    # Page-level layout compositions (optional — Views can serve this role)
  └── pages/        # Route-level views wired to Vue Router
  ```

  **Atoms** (`components/atoms/`): The smallest UI building blocks. No business logic. No store access. Pure props in, events out. Examples: `ChatInput`, `ChatMessage`, `ScorerSlider`, `StarRating`, `ToggleSwitch`, `TypePills`, `NumberStepper`. An atom renders a single visual element with styling variants via props. If you can't describe what it renders in 3 words ("a styled button"), it's not an atom.

  **Molecules** (`components/molecules/`): Simple, focused combinations of atoms that form a single functional unit. Minimal or no store access — prefer props/events. Examples: `RecCard` (atoms: title + badges + score details), `EditModal` (atoms: star rating + season checklist + form fields), `LibraryCard` (atoms: title + badges + action buttons), `OAuthConnectFlow` (atoms: button + input). A molecule answers one user question ("what's the rating?" / "what weight?").

  **Organisms** (`components/organisms/`): Complex, self-contained UI sections composed of molecules and atoms. Organisms MAY access stores directly — they are the integration layer between the data layer and the presentational atoms/molecules. Examples: `AppSidebar`, `RecCard`, `LibraryCard`, `EditModal`, `ChatMessage`, `LibraryFilters`, `EnrichmentCard`, `MemoryPanel`, `ProfilePanel`. An organism represents a distinct section of the UI that could be described as a "region" of the page.

  **Pages** (`components/pages/`): Route-level views wired to Vue Router via `router/index.ts`. Pages compose organisms, handle route-level lifecycle (`onMounted`, `watch` on route params/user changes), and wire stores to organisms. They should NOT contain complex rendering logic — they are orchestrators. Examples: `RecommendationsPage`, `LibraryPage`, `ChatPage`, `DataPage`, `PreferencesPage`. Named with `*Page.vue` suffix (not `*View.vue` — "View" is ambiguous in Vue).

  **Rules:**
  - Atoms NEVER import molecules, organisms, or pages. Molecules NEVER import organisms or pages. The dependency arrow only points downward.
  - Atoms and molecules NEVER access Pinia stores directly. They receive data via props and communicate via events. This makes them testable in isolation and reusable across features.
  - If a component is used in only one organism and has no reuse potential, it can live alongside that organism. But the moment it's used in two places, it must be extracted to the correct atomic level.
  - An organism that grows beyond ~150 lines of `<script setup>` likely needs decomposition into smaller organisms or extraction of molecules.
  - **Violations of the atomic hierarchy are CRITICAL findings.** An atom that imports a store, a molecule that renders an entire page section, or a page with 300 lines of template — these are structural problems that compound over time.

- **`v-html` is a security boundary.** Every `v-html` usage must route through DOMPurify with an explicit allowlist. `v-html` on static/hardcoded content is the wrong pattern — use inline template markup instead. Flag any `v-html` that does not go through sanitization as CRITICAL.
- **Reactive values must be reactive.** A `const x = props.foo.bar` captures the value once at component creation. If the prop changes (e.g., during SSE streaming), the const goes stale. Use `computed(() => props.foo.bar)` for any derived value from props that can change. This is a functional bug, not a style issue.
- **`defineProps` and `defineEmits` must use TypeScript generics** (`defineProps<{ ... }>()`, `defineEmits<{ ... }>()`), not the runtime declaration syntax. This is the project convention.
- **Component decomposition**: Each `.vue` file should do one thing. If a component has more than ~150 lines of `<script setup>`, it probably needs splitting. But don't over-split — a component used in exactly one place with no reuse potential should stay inline.
- **Scoped styles vs global CSS**: Utility/shared styles go in `resources/css/base.css`. If the same CSS class appears in `<style scoped>` blocks of two or more components, it belongs in `base.css`. Duplicated scoped styles are a DRY violation.

### Pinia Store Patterns

- **Setup function syntax only** (`defineStore('name', () => { ... })`). Options API stores are not used in this project.
- **Stores must not leak timers.** Any `setInterval` or `setTimeout` in a store must have a corresponding cleanup function that is called from the consuming component's `onUnmounted`. A timer that runs forever is a resource leak.
- **Stores should not import other stores at module level** if it creates circular dependencies. Import inside the action that needs it.
- **Return statement format**: One property per line, grouped by state/getters/actions. Follow the pattern established by existing stores.

### Composable Patterns

- **Composables are for reusable stateful logic.** A composable that wraps a single function call adds indirection without value — just export the function directly.
- **`onUnmounted` cleanup**: Any composable that registers event listeners, observers, or timers must clean them up in `onUnmounted`.

### TypeScript Specifics

- **No `as` type assertions to bypass the type system.** `as unknown as Foo` is a code smell — it means the types are wrong. Fix the types, don't cast around them. Exception: test mocks where the full interface is intentionally partial.
- **Use `unknown` over `any`.** `any` disables type checking. `unknown` forces you to narrow before use. This is not optional.
- **Template type safety**: Use typed event handlers (`($event.target as HTMLSelectElement).value` is acceptable in templates where TS cannot infer the element type).
- **No unused imports** — TypeScript's `isolatedModules` catches some of these, but not all. Watch for Vue imports (`ref`, `computed`, `watch`, `onMounted`, etc.) that are imported but not used.

### CSS / Tailwind

- **CSS custom properties (`:root` vars in `base.css`) are the theming source of truth.** Components must use these vars (directly or via Tailwind `@theme` mappings), never hardcode colors.
- **Tailwind `@theme` mappings must not be self-referential.** `--color-foo: var(--color-foo)` is a no-op that creates a circular reference. If the `:root` var already uses the Tailwind naming convention, no mapping is needed.

### Frontend Performance

- **Full-library imports are banned.** `import _ from 'lodash'` or `import * as _ from 'lodash'` pulls the entire library into the bundle when you need one function. `import debounce from 'lodash/debounce'` is the correct import. This applies to every library — if you can import the specific function or submodule, you must. Every unnecessary kilobyte in the bundle is paid for by every user on every page load. **Severity: HIGH.**
- **Page routes must be lazy loaded.** `import FooPage from './FooPage.vue'` in the router is a static import that defeats code splitting entirely. Every page-level route must use `() => import('./FooPage.vue')`. Static imports of page components in the router are an automatic finding. **Severity: HIGH.**
- **No function calls in `v-for` templates.** `{{ formatDate(item.date) }}` inside a `v-for` re-executes on every render cycle. If the result depends only on the item data, pre-compute it (map the list to include the formatted value) or use a computed property. Functions called in templates are invisible performance drains that compound with list size. **Severity: MEDIUM.**
- **Stale reactive captures are bugs, not style issues.** The rule about `const x = props.foo.bar` capturing once extends to: `watch` sources that destructure props outside the callback (stale closure), missing `toRefs` when destructuring props in composables, and `ref` values captured in closures registered during `onMounted` that will never see updates. If a value comes from a reactive source and is used in a context that expects reactivity, it must remain reactive. **Severity: HIGH.**
