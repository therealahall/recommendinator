# Theme Development Guide

A theme is a folder in `src/web/static/themes/`, or in `private/themes/` for one
you do not want in the repo, that overrides CSS color variables.

## How theming works

- **A theme's `colors.css` is the palette**, declaring the whole colour contract
  on `:root` through a `<link>`.
- **`resources/css/base.css` is a fallback, not a theme.** Its `:root` holds
  unbranded greys, so a `colors.css` that omits a token degrades to something
  legible rather than to half of another palette.
- Base tokens sit in `@layer tokens`; a theme arrives unlayered and so outranks
  them whichever order the two stylesheets land in.
- Both blocks must be on `:root`. A token whose value holds `var()` is
  substituted on the element declaring it, so a base block on an ancestor would
  freeze every derived token at the fallback greys.
- Themes are **not part of the Vite build**. The server renders the stored
  theme's `<link>` into the page it serves, after the bundle it overrides, so
  the first paint is already themed. A `<link>` and not an inline `<style>`,
  which the CSP forbids. Switching swaps that link's `href`.
- The served `<html>` carries `data-theme` and `data-theme-type`, kept in step
  by the theme store.

So an override needs no rebuild: every rule in the app reads the custom
properties, and the `<link>` redeclares them.

## Layout

```
src/web/static/themes/     # or private/themes/, which is gitignored
└── my-theme/
    ├── theme.json      # required: metadata
    ├── colors.css      # required: color variable overrides
    ├── README.md       # recommended: design notes
    └── preview.png     # optional: screenshot
```

A private theme is listed and served exactly like a shipped one, from
`/static/private-themes/`. An id already taken by a shipped theme is refused,
and so is a folder name outside `[A-Za-z0-9_-]`: the id reaches an `href`. A
theme is listed and served the moment it is dropped in, restart or no.

## theme.json

Every field is required.

```json
{
    "name": "My Theme",
    "description": "Short description of the theme",
    "author": "Your Name",
    "version": "1.0.0",
    "type": "dark"
}
```

| Field | Description |
|-------|-------------|
| `name` | Display name in the theme switcher |
| `description` | Brief description |
| `author` | Author, or "Built-in" for included themes |
| `version` | Semantic version |
| `type` | `"dark"` or `"light"`; anything else is not a theme. Becomes `data-theme-type` |

## Color variables

Declare these in `colors.css` under a `:root` selector. The Nord column is what
the default theme sets; a token you leave out falls back to base.css's unbranded
grey, not to Nord, so declare the whole set. A row marked _derived_ is not part
of that set: `base.css` computes it from the tokens above, and a theme shipped in
this repo that declares one fails the token contract, which allows only what Nord
declares.

Five tokens were renamed for role rather than appearance, and a theme still
declaring an old name silently paints the unbranded fallback. A private theme
predating the rename needs `--accent-light` → `--accent-hover`, `--overlay-dark`
→ `--overlay-backdrop`, `--overlay-medium` → `--overlay-scrim`,
`--text-on-dark-fill` → `--text-on-emphasis`; `--accent-teal` is gone and no rule
ever read it.

### Backgrounds

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--bg-primary` | `#2e3440` | Page background |
| `--bg-card` | `#3b4252` | Card backgrounds |
| `--bg-sidebar` | `#2e3440` | The rail, the tab bar and the top strip are mixed from this |
| `--bg-elevated` | `#434c5e` | Elevated surfaces |
| `--bg-input` | `#2e3440` | Input fields |
| `--bg-hover` | `#434c5e` | Hover states |
| `--bg-secondary` | `#3b4252` | Secondary surfaces (code blocks) |
| `--bg-active` | _derived_ `color-mix(in srgb, var(--accent) 20%, transparent)` | Active/selected |

### Text

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--text-primary` | `#eceff4` | Primary text |
| `--text-secondary` | `#d8dee9` | Secondary/dimmer text |
| `--text-muted` | `#b3c7da` | Muted/label text |
| `--text-inverse` | `#2e3440` | Text on accent backgrounds |
| `--text-on-emphasis` | `#ffffff` | Labels on the Delete and Enable fills, dark in either theme |

`--text-muted` carries help text, hints and empty states at 12-13px, so keep it
at 4.5:1 or better against `--bg-primary`, `--bg-card`, `--bg-sidebar`,
`--bg-elevated`, `--bg-input` and `--chrome`, the rail material mixed from
`--bg-sidebar` (WCAG 1.4.3). The lightest of those in a dark theme, and the
darkest in a light one, is the one that binds.

### Accents

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--accent` | `#81a1c1` | Buttons, links, active states |
| `--accent-hover` | `#88c0d0` | A primary button or checked pill under the pointer, and at rest the weight slider's thumb and filled track |
| `--focus-ring` | _derived_ `var(--accent-hover)` | The keyboard focus ring |

`--focus-ring` is the one focus indicator the whole app uses, drawn just outside
the control, and `base.css` derives it so a theme declares one accent rather than
two. Keep `--accent-hover` at 3:1 or better against every surface a ring can
land on — `--bg-card`, `--bg-input`, `--bg-primary`, `--bg-elevated`,
`--bg-sidebar`, `--chrome` and the error status bar's tint, where Try again
sits — or
keyboard users lose the only cue telling them where they are (WCAG 1.4.11).

Both accents are also fills under `--text-inverse`: `--accent` on a primary
button or active pill at rest, `--accent-hover` on the same two hovered. Each
owes that pairing 4.5:1 (WCAG 1.4.3), and the resting one binds tighter.

### Borders

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--border-default` | `color-mix(in srgb, #4c566a 45%, var(--text-primary))` | The edge of a button, accordion, menu or drop zone |
| `--border-subtle` | `#434c5e` | Subtle/secondary borders |
| `--border-focus` | _derived_ `var(--accent)` | Border of a focused field |
| `--border-interactive` | _derived_ `var(--border-default)` | The edge of every field, select, pill and toggle |

Both separate a control from its own fill and from the surface behind it, so
each owes 3:1 against every background a control lands on (WCAG 1.4.11).
`--border-default` is pulled toward `--text-primary` to earn that, so a theme
setting its text colour inherits an edge and need not override either. A private
theme, which the token contract does not police, may declare
`--border-interactive` alone to give fields a heavier edge than the buttons and
menus that keep `--border-default`.

### Semantic

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--color-success` | `#a3be8c` | Completed, unignore |
| `--color-warning` | `#ebcb8b` | Unread badge, rating stars, ignored badge |
| `--color-error` | `#bf616a` | Danger buttons, failures |
| `--color-info` | _derived_ `var(--accent)` | Loading, sync |

These are sized for fills and fall under 4.5:1 as text, so `base.css` derives
`--color-success-text`, `--color-error-text`, `--color-info-text` and
`--color-warning-text` by mixing each toward `--text-primary`. Override the fill
and the text colour follows.

### Overlays and shadows

| Variable | Nord | Used for |
|----------|---------|-------------|
| `--overlay-backdrop` | `rgba(0, 0, 0, 0.6)` | Modal backdrops |
| `--overlay-scrim` | `rgba(0, 0, 0, 0.5)` | The scoring weights scrim |
| `--shadow-sm` | `0 1px 2px rgba(0, 0, 0, 0.3)` | Rung 1: a surface resting on the stage |
| `--shadow-md` | `0 2px 8px rgba(0, 0, 0, 0.3)` | Rung 2: a surface summoned over it |
| `--shadow-lg` | `0 4px 16px rgba(0, 0, 0, 0.4)` | Rung 3: a modal surface |

`base.css` aliases these to `--elevation-1`, `--elevation-2` and `--elevation-3`,
and no rule outside that alias names a shadow. Retuning one moves every surface
on its rung together.

## Transparent variants come free

The stylesheet derives them with `color-mix()`:

```css
/* resources/css/base.css */
.badge[data-tone='success'] {
    background: color-mix(in srgb, var(--color-success) 20%, transparent);
    color: var(--text-primary);
}
```

Override `--color-success` and those badges follow. Do not define the transparent
variants yourself.

## Creating a theme

1. `mkdir src/web/static/themes/my-theme`, or `private/themes/my-theme`.
2. Write `theme.json`, per the schema above.
3. Write `colors.css`. Copy `themes/nord/colors.css` and retune it: that file is
   the whole contract, and anything you drop paints an unbranded grey. Keep its
   `color-scheme`, which is what makes the browser paint native controls to
   match:

   ```css
   :root {
       color-scheme: dark;
       --accent: #e06c75;
       --accent-hover: #e5c07b;
       --color-success: #98c379;
       --color-warning: #d19a66;
       --color-error: #be5046;
   }
   ```

4. Start the server, select the theme from the Preferences tab or with
   `theme set`, and check every page for text readability, badge contrast and
   button visibility. `resources/css/contrast.test.ts` measures every floor
   above for each theme in `src/web/static/themes/`.
5. Optionally add a `README.md` describing your design choices.

## What a theme may and may not set

A theme owns colour, the two font families (`--font-ui`, `--font-display`),
radius (`--radius-*`) and depth (`--shadow-*`).

Everything else is core, declared only in `base.css`: spacing (`--space-*`), the
type scale (`--text-*`), line height, weight, tracking, the elevation rungs
(`--elevation-*`), transitions, `--rail-w`, `--tabbar-h`, `--topbar-h` and
`--measure`. That fence stops a palette undoing a target size or the width text
is set to.

## Persistence

The selection is a row in `user_ui_settings`, one per user, so it follows them
across browsers and `preferences reset` leaves it alone. Both interfaces reach
it: `theme show` / `theme set` and `GET` / `PUT /api/users/{id}/theme`.

The server reads the row before serving the page, so no request decides the
first paint; `localStorage` still caches it for the Vite dev server. A user who
has picked nothing is painted in the default theme, and the shell swaps to the
first installed theme matching the OS `prefers-color-scheme` only when the
default is of the other kind — before first paint, so there is no flash. A
stored pick is never overridden.
