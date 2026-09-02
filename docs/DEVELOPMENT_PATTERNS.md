# Development Patterns

Operational playbook for this repo. Read it at the start of any feature, bug fix
or plan-mode task.

## Plan mode

**When a plan is approved, create a bead for each step.** After `ExitPlanMode`:

1. `bd create` per step, in parallel calls: `--title` matching the step summary,
   `--description` carrying the implementation notes, a `--type` (`task`,
   `feature`, `chore`), and `--priority=2` unless the step is critical (0) or low
   (4).
2. `bd dep add` where order matters.
3. Work them in order, `in_progress` before starting and `close` when done.

```bash
bd create --title="Add parser for new source" --description="..." --type=task --priority=2
bd dep add <tests-id> <parser-id>
```

## Adding a feature

**Think before acting.** Ask when requirements are ambiguous, several approaches
are valid, or the scope is unclear.

Read the docs, search for existing patterns and match them, ask about any
trade-off worth deciding, write tests first, implement, run
`command make check`, update the docs (ARCHITECTURE.md, README.md, QUICKSTART.md,
CLAUDE.md, `docs/`), commit atomically in conventional format.

## Adding a data source

1. Create the plugin in `src/ingestion/sources/`, following `roms.py` or
   `steam.py`, yielding `ContentItem` objects.
2. Add tests with mocked APIs.
3. Wire CLI and web.
4. Update ARCHITECTURE.md, README.md, docs/DATA_SOURCES.md and
   docs/PLUGIN_DEVELOPMENT.md. **Not** `config/example.yaml`, which is
   bootstrap-only. Sources are configured from the Data tab and the `source` CLI,
   off the plugin's `get_config_schema()`.

## Bug fixes

1. Write the regression test first. It must fail before the fix.
2. Fix the bug.
3. Update the docs if behaviour changed or was documented wrongly.
4. Commit with the `fix` type.

## Anti-churn checklist (run BEFORE writing code)

Review agents flag the same things over and over. Read this list first and search
for the patterns it names.

### Before writing any code

1. **Grep for a sibling that already does this.** New CLI subcommand? Read the
   group's module in `src/cli/commands/` end to end, and `src/cli/_shared.py`
   for the helpers every group uses. New endpoint? Read a sibling in that
   capability's module under `src/web/api/`, and `_shared.py` beside it.
   New test file? Read its nearest neighbour. Match structure
   exactly: import order, helper usage, naming, error handling, output branching.
2. **Search `src/utils/` and module-local helpers before writing a helper.** If
   two sites would use it, it exists already or belongs there.
   `extract_tv_season_fields`, `_item_to_dict`, `get_feature_flags`,
   `list_merge`, `series`, `sorting`.
3. **For CLI changes, diff against the web API first.** The two are mirrors.
   Read the endpoint's Pydantic response model and request params. The JSON shape
   MUST match exactly: same keys, same types, same empty-result behaviour.
4. **For test changes, read `tests/<area>/conftest.py` first.** Use its shared
   fixtures (`cli_runner`, `_invoke_with_mocks`, `_cli_patches`) rather than
   redefining one locally. The root `conftest.py` already redirects the credential
   key, neutralises production logging, pins the timezone to UTC and narrows the
   file-import allowlist to the test's own `tmp_path`, for every test in every
   tree, plugin-local ones included, so no test arranges those itself. A test
   needing another zone requests `host_timezone`; one reading a repository
   directory requests `allowed_source_roots`. Both are restored either way.

### Imports

- **All imports at module top.** No function-level imports, no `import x as _x`
  inside a test method.
- **Do not reorder imports by hand.** Let ruff/isort do it. When ruff splits a
  block you need kept together, `# noqa: I001` goes on the
  `from __future__ import annotations` line.

### Tests

- **`spec=RealClass` on every MagicMock** standing in for a real type
  (RecommendationEngine, ContentItem). A bare `MagicMock()` is almost always
  wrong. Mocked storage comes from `tests.factories.make_storage_mock`, which
  specs the sub-stores as well.
- **Use the shared `_invoke_with_mocks` / `_cli_patches`** from
  `tests/cli/conftest.py`. Nesting `with patch():` more than two deep means you
  are duplicating conftest.
- **`ContentItem` requires `status`**, and most tests need `rating` too. On
  "Missing named argument", read the dataclass signature rather than guessing.
- **A test's name is what says which regression it catches.** No bare tests with
  cryptic names, and no docstring restating the name.
- **Assertions must be strong.** `assert result.exit_code == 0` is necessary and
  not sufficient. Assert specific output strings, mock call args or parsed JSON
  keys. "Output is non-empty" is not an assertion.

### CLI/web parity (parity-review blocks on ANY drift)

- **JSON output on empty results is `[]` or the empty response object**, never a
  text message. Every `if not items: click.echo("No ...")` needs an
  `if output_format == "json"` branch emitting valid empty JSON first.
- **Progress chatter goes to stderr**, `click.echo(..., err=True)`. stdout is
  the data channel, and a progress line printed ahead of the `--format json`
  branch breaks every piped caller.
- **The JSON field set must match the web Pydantic response exactly.** Diff the
  key sets against the matching `*Response` model in `src/web/api/`. Missing or
  extra fields are blocking drift.
- **Every CLI command needs `--format json` if the web API returns JSON**, and
  every web API param needs a matching CLI option. No exceptions.
- **Serialization goes through the shared helpers** (`_item_to_dict`,
  `extract_tv_season_fields`). Never hand-roll the dict in both places.

### Code style

- **No backwards-compat shims, no "removed" comments, no unused `_var` renames.**
  Delete the code.
- **Default to no comments.** Comment the WHY of a non-obvious constraint only.
  Never the WHAT, never "added for task X", never the current review round.
- **No try/except that re-raises unchanged**, and no validation for impossible
  internal states. Trust internal code. Validate at system boundaries.
- **No feature flags or config toggles unless asked for.** Just change the code.
- **Click `IntRange` for bounded ints, Click choices for enums.** Do not
  hand-validate simple bounds. Do validate config-driven limits such as
  `max_count`, which Click cannot express.
- **Never sanitize an argv value at a call site.** `SurrogateFreeGroup` in
  `src/cli/main.py` strips lone surrogates from every token before Click binds
  it, so a per-option `strip_lone_surrogates` is dead weight — and the option
  added next week would be the one nobody remembered to guard. Same for a
  plugin's items: `_upsert_content_item` escapes them before any bind, so a
  per-plugin guard is dead weight for the same reason.
- **No outside-the-repository paths in docs, tooling, CI, compose and build
  files, `tests/` or `.claude/`.** Not in prose, comments, docstrings, report
  strings or tests. Neither a reader nor CI can verify a path they cannot see.
  `src/` is exempt, because application code addresses the machine it runs on.

### Shell discipline

- **One Bash call per logical step.** Never chain with `&&` or `;`.
- **Run `make format` and `uv run --locked --extra dev python -m ruff check src/
  tests/ conftest.py --fix` BEFORE `command make check`**, so it cannot fail on
  auto-fixable formatting. `private/` is gitignored, so `make format` hands
  black those files by name and ruff needs a `--no-respect-gitignore private/
  --fix` pass.
- **Do not read raw subagent output files.** The agent returns its finding in its
  tool result.
- **Never poll or wait on background agents.** The runtime notifies on completion.

### If an agent flags something, fix the CLASS, not the instance

Search the rest of the diff for the same pattern, fix every occurrence in one
pass, then grep the diff to confirm it is gone. Do not wait for round N+1 to find
the same issue elsewhere.

## Frontend conventions (Vue 3 / TypeScript)

The frontend lives in `resources/js/` and `resources/css/`. Every general
code-quality rule applies to TypeScript and Vue too. The project-specific rules
below live here rather than in the project-agnostic agents.

### Atomic Design

Component organization follows Brad Frost's Atomic Design. This is not optional.

```
components/
├── atoms/        # Smallest indivisible UI elements
├── molecules/    # Simple groups of atoms working together
├── organisms/    # Complex, distinct UI sections
├── templates/    # Page-level layout compositions (optional)
└── pages/        # Route-level views wired to Vue Router
```

**Atoms** render one visual element, with variants via props. No business logic,
no store access, props in and events out: `StarRating`, `ToggleSwitch`,
`NumberStepper`. If you cannot describe what it renders in three words, it is not
an atom.

**Molecules** combine atoms into one functional unit answering one user question
("what's the rating?"): `RecCard`, `EditModal`, `OAuthConnectFlow`. Minimal or no
store access, prefer props and events.

**Organisms** are self-contained regions of the page, composed of molecules and
atoms, and MAY access stores directly. They are the integration layer between
data and presentation: `AppNav`, `LibraryFilters`, `ProfilePanel`.

**Pages** are route-level orchestrators wired through `router/index.ts`. They
compose organisms, handle route lifecycle (`onMounted`, `watch` on route params
or user changes) and wire stores to organisms. No complex rendering logic. Named
`*Page.vue`, never the ambiguous `*View.vue`.

Rules:

- The dependency arrow points downward only. Atoms never import molecules,
  organisms or pages. Molecules never import organisms or pages.
- Atoms and molecules never touch Pinia stores. Data in via props, out via events.
- A component used in exactly one organism, with no reuse potential, can live
  beside it. The moment it is used twice, extract it to its atomic level.
- An organism past ~150 lines of `<script setup>` needs decomposing.
- **Violations of the hierarchy are CRITICAL findings**: an atom importing a
  store, a molecule rendering a page section, a page with 300 lines of template.

### Vue

- **Live regions are mounted persistently, and only their text changes.** Some
  screen readers, JAWS especially, treat a region that appears already populated
  as page content rather than a status change and skip it entirely (WCAG 4.1.3).
  Render it unconditionally, `sr-only` if it should not be seen, and bind its
  text to a computed that is `''` when there is nothing to announce. **`v-if` is
  the bug, `v-show` is not a substitute**: `display: none` removes the node from
  the accessibility tree.
- **Do not double up.** A visible notice and an `sr-only` region carrying
  the same words announce twice, so only one gets the live role.
- **`v-html` is a security boundary.** Every use routes through DOMPurify with an
  explicit allowlist. On static content it is the wrong pattern, so use inline
  template markup. Unsanitized `v-html` is CRITICAL.
- **Reactive values must stay reactive.** `const x = props.foo.bar` captures once
  at component creation and goes stale when the prop changes, after a store
  refetch for instance. Use `computed(() => props.foo.bar)`. This is a
  functional bug, not a style issue.
- **`defineProps` and `defineEmits` use TypeScript generics**,
  `defineProps<{ ... }>()`, not the runtime declaration syntax.
- **One job per `.vue` file.** Past ~150 lines of `<script setup>`, split it. Do
  not over-split: a component used in one place with no reuse potential stays
  inline.
- **Shared styles go in `resources/css/base.css`.** The same class in two
  components' `<style scoped>` blocks is a DRY violation.

### Pinia stores

- **Setup function syntax only**, `defineStore('name', () => { ... })`. Options
  API stores are not used here.
- **Stores must not leak timers.** Every `setInterval` or `setTimeout` needs a
  cleanup function called from the consuming component's `onUnmounted`.
- **No module-level store imports** where that creates a cycle. Import inside the
  action that needs it.
- **Return statement format**: one property per line, grouped state, getters,
  actions, following the existing stores.

### Composables

- **Composables are for reusable stateful logic.** One wrapping a single function
  call is indirection. Export the function.
- **Clean up in `onUnmounted`**: event listeners, observers, timers.

### TypeScript

- **No `as` assertions to bypass the type system.** `as unknown as Foo` means the
  types are wrong. Fix them. Exception: test mocks that are intentionally partial.
- **`unknown` over `any`.** `any` disables checking, `unknown` forces narrowing.
  Not optional.
- **Typed event handlers.** `($event.target as HTMLSelectElement).value` is
  acceptable in templates, where TS cannot infer the element type.
- **No unused imports.** `isolatedModules` catches some. Watch the Vue ones
  (`ref`, `computed`, `watch`, `onMounted`).

### CSS

- **The `:root` vars in `base.css` are the theming source of truth.** Components
  use them. Never hardcode a colour — a theme's `colors.css` can only reach what
  a var declares.

### Frontend performance

- **Full-library imports are banned. HIGH.** `import _ from 'lodash'` pulls the
  whole library in for one function. `import debounce from 'lodash/debounce'` is
  correct, and the rule holds for every library.
- **Page routes must be lazy loaded. HIGH.** A static
  `import FooPage from './FooPage.vue'` in the router defeats code splitting. Use
  `() => import('./FooPage.vue')`.
- **No function calls in `v-for` templates. MEDIUM.**
  `{{ formatDate(item.date) }}` re-executes every render. Pre-compute it into the
  list or use a computed.
- **Stale reactive captures are bugs, not style. HIGH.** The
  `const x = props.foo.bar` rule extends to `watch` sources destructuring props
  outside the callback, missing `toRefs` when destructuring props in composables,
  and refs captured in closures registered during `onMounted`. A value from a
  reactive source, used where reactivity is expected, must stay reactive.
