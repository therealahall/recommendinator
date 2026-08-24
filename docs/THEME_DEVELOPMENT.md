# Theme Development Guide

A theme is a folder in `src/web/static/themes/`, or in `private/themes/` for one
you do not want in the repo, that overrides CSS color variables.

## How theming works

- **`:root` variables** in `resources/css/base.css` are the source of truth.
- **A theme's `colors.css`** overrides the `:root` variables through a `<link>`.
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

Override these in `colors.css` under a `:root` selector. Override only what you
want to change. Anything unset keeps its dark-theme default.

### Backgrounds

| Variable | Default | Used for |
|----------|---------|-------------|
| `--bg-primary` | `#2e3440` | Page background |
| `--bg-card` | `#3b4252` | Card backgrounds |
| `--bg-sidebar` | `#2e3440` | Sidebar |
| `--bg-elevated` | `#434c5e` | Elevated surfaces |
| `--bg-input` | `#2e3440` | Input fields |
| `--bg-hover` | `#434c5e` | Hover states |
| `--bg-secondary` | `#3b4252` | Secondary surfaces (code blocks) |
| `--bg-active` | `color-mix(in srgb, var(--accent) 20%, transparent)` | Active/selected, derived from the accent |

### Text

| Variable | Default | Used for |
|----------|---------|-------------|
| `--text-primary` | `#eceff4` | Primary text |
| `--text-secondary` | `#d8dee9` | Secondary/dimmer text |
| `--text-muted` | `#b3c7da` | Muted/label text |
| `--text-inverse` | `#2e3440` | Text on accent backgrounds |

`--text-muted` carries help text, hints and empty states at 12-13px, so keep it
at 4.5:1 or better against `--bg-primary`, `--bg-card`, `--bg-sidebar`,
`--bg-elevated` and `--bg-input` (WCAG 1.4.3). The lightest of those in a dark
theme, and the darkest in a light one, is the one that binds.

### Accents

| Variable | Default | Used for |
|----------|---------|-------------|
| `--accent` | `#81a1c1` | Buttons, links, active states |
| `--accent-light` | `#88c0d0` | Highlights, and the keyboard focus ring |
| `--accent-teal` | `#8fbcbb` | Supplementary |

`--accent-light` is the one focus indicator the whole app uses, drawn just
outside the control. Keep it at 3:1 or better against every surface a ring can
land on — `--bg-card`, `--bg-input`, `--bg-primary`, `--bg-elevated`,
`--bg-sidebar` and the error status bar's tint, where Try again sits — or
keyboard users lose the only cue telling them where they are (WCAG 1.4.11).

Both accents are also fills under `--text-inverse`: `--accent` on a primary
button or active pill at rest, `--accent-light` on the same two hovered. Each
owes that pairing 4.5:1 (WCAG 1.4.3), and the resting one binds tighter. The
shipped Snowstorm misses it at 4.03:1, tracked in `qs5i.2.47`; the suite
measures the hovered pair only, so check `--accent` by hand.

### Borders

| Variable | Default | Used for |
|----------|---------|-------------|
| `--border-default` | `#4c566a` | Dividers and decorative edges |
| `--border-subtle` | `#434c5e` | Subtle/secondary borders |
| `--border-focus` | `var(--accent)` | Border of a focused field |
| `--border-interactive` | `color-mix(in srgb, var(--border-default) 50%, var(--text-primary))` | The edge of every field, select, pill and toggle |

`--border-interactive` is what separates an editable control from its own fill
and from the surface behind it, so it owes 3:1 against `--bg-card`, `--bg-input`
and `--bg-elevated` (WCAG 1.4.11). It is derived from the two tokens it sits
between, so a theme that sets those inherits it and need not override it.

### Semantic

| Variable | Default | Used for |
|----------|---------|-------------|
| `--color-success` | `#a3be8c` | Completed, unignore |
| `--color-warning` | `#ebcb8b` | Unread badge, rating stars, ignored badge |
| `--color-error` | `#bf616a` | Danger buttons, failures |
| `--color-info` | `var(--accent)` | Loading, sync |

These are sized for fills and fall under 4.5:1 as text, so `base.css` derives
`--color-success-text`, `--color-error-text`, `--color-info-text` and
`--color-warning-text` by mixing each toward `--text-primary`. Override the fill
and the text colour follows.

### Overlays and shadows

| Variable | Default | Used for |
|----------|---------|-------------|
| `--overlay-dark` | `rgba(0, 0, 0, 0.6)` | Modal backdrops |
| `--overlay-medium` | `rgba(0, 0, 0, 0.5)` | Sidebar mobile overlay |
| `--shadow-sm` | `0 1px 2px rgba(0, 0, 0, 0.3)` | Small shadow |
| `--shadow-md` | `0 2px 8px rgba(0, 0, 0, 0.3)` | Medium shadow |
| `--shadow-lg` | `0 4px 16px rgba(0, 0, 0, 0.4)` | Large shadow |
| `--shadow-tooltip` | `0 4px 12px rgba(0, 0, 0, 0.25)` | Tooltip shadow |

## Transparent variants come free

The stylesheet derives them with `color-mix()`:

```css
/* resources/css/base.css */
.badge-status {
    background: color-mix(in srgb, var(--color-success) 10%, transparent);
    border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
}
```

Override `--color-success` and those badges follow. Do not define the transparent
variants yourself.

## Creating a theme

1. `mkdir src/web/static/themes/my-theme`, or `private/themes/my-theme`.
2. Write `theme.json`, per the schema above.
3. Write `colors.css`, declaring `color-scheme` so the browser paints native
   controls and scrollbars to match:

   ```css
   :root {
       color-scheme: dark;
       --accent: #e06c75;
       --accent-light: #e5c07b;
       --color-success: #98c379;
       --color-warning: #d19a66;
       --color-error: #be5046;
   }
   ```

4. Start the server, select the theme from the Preferences tab or with
   `theme set`, and check every page for text readability, badge contrast and
   button visibility. `resources/css/contrast.test.ts` measures every floor
   above for each shipped theme, except the resting `--accent` one it names.
5. Optionally add a `README.md` describing your design choices.

## What themes cannot override

Color variables only. Spacing (`--space-*`), typography (`--font-*`, `--text-*`),
radius (`--radius-*`), transitions (`--transition-*`) and layout dimensions
(`--sidebar-width`) are fixed.

## Persistence

The selection is a row in `user_ui_settings`, one per user, so it follows them
across browsers and `preferences reset` leaves it alone. Both interfaces reach
it: `theme show` / `theme set` and `GET` / `PUT /api/users/{id}/theme`. The
server reads the row before serving the page, so no request decides the first
paint; `localStorage` still caches it for the Vite dev server. A user who has
picked nothing is stored empty and painted `nord`.
