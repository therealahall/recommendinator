# Theme Development Guide

A theme is a folder in `src/web/static/themes/` that overrides CSS color
variables.

## How theming works

- **`:root` variables** in `resources/css/base.css` are the source of truth.
- **Tailwind `@theme`** mappings in `resources/css/tailwind.css` bridge those
  vars into utility classes such as `bg-bg-primary` and `text-text-primary`.
- **A theme's `colors.css`** overrides the `:root` variables through a
  dynamically loaded `<link>`.
- Themes are **not part of the Vite build**. They are served from
  `/static/themes/` and loaded at runtime.

So an override works identically under Tailwind utilities and raw CSS. Both read
the same custom properties, and no Tailwind rebuild is needed. `tailwind.css` is
a bridge layer, and a theme author never touches it:

```css
@theme {
  --color-bg-primary: var(--bg-primary);
  --color-text-primary: var(--text-primary);
  --color-accent: var(--accent);
}
```

## Layout

```
src/web/static/themes/
└── my-theme/
    ├── theme.json      # required: metadata
    ├── colors.css      # required: color variable overrides
    ├── README.md       # recommended: design notes
    └── preview.png     # optional: screenshot
```

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
| `type` | `"dark"` or `"light"`, an informational label |

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
| `--text-muted` | `#97abbe` | Muted/label text |
| `--text-inverse` | `#2e3440` | Text on accent backgrounds |

### Accents

| Variable | Default | Used for |
|----------|---------|-------------|
| `--accent` | `#81a1c1` | Buttons, links, active states |
| `--accent-light` | `#88c0d0` | Highlights, and the keyboard focus ring |
| `--accent-teal` | `#8fbcbb` | Supplementary |

### Borders

| Variable | Default | Used for |
|----------|---------|-------------|
| `--border-default` | `#4c566a` | Standard borders |
| `--border-subtle` | `#434c5e` | Subtle/secondary borders |
| `--border-focus` | `var(--accent)` | Border of a focused field |

### Semantic

| Variable | Default | Used for |
|----------|---------|-------------|
| `--color-success` | `#a3be8c` | Completed, unignore |
| `--color-warning` | `#ebcb8b` | Unread badge, rating stars, ignored badge |
| `--color-error` | `#bf616a` | Danger buttons, failures |
| `--color-info` | `var(--accent)` | Loading, sync |

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

1. `mkdir src/web/static/themes/my-theme`
2. Write `theme.json`, per the schema above.
3. Write `colors.css`:

   ```css
   :root {
       --accent: #e06c75;
       --accent-light: #e5c07b;
       --color-success: #98c379;
       --color-warning: #d19a66;
       --color-error: #be5046;
   }
   ```

4. Start the server, select the theme from the Preferences tab, and check every
   page for text readability, badge contrast and button visibility.

   `--accent-light` is the one focus indicator the whole app uses, drawn just
   outside the control. Keep it at 3:1 or better against `--bg-card`,
   `--bg-input` and `--bg-primary` — the three surfaces a ring around a field
   lands on — or keyboard users lose the only cue telling them where they are
   (WCAG 1.4.11). `resources/css/base.css.test.ts` measures this for every
   shipped theme.
5. Optionally add a `README.md` describing your design choices.

## What themes cannot override

Color variables only. Spacing (`--space-*`), typography (`--font-*`, `--text-*`),
radius (`--radius-*`), transitions (`--transition-*`) and layout dimensions
(`--sidebar-width`) are fixed.

## Persistence

The selection is saved per user, so it follows them across browsers and devices.
`localStorage` caches it for fast first paint before preferences load. New users
get `nord`.
