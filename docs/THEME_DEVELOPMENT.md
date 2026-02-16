# Theme Development Guide

Create custom themes for the Personal Recommendations web interface. Each theme is a folder in `src/web/static/themes/` containing color overrides.

## Theme Directory Structure

```
src/web/static/themes/
└── my-theme/
    ├── theme.json      # Required — theme metadata
    ├── colors.css      # Required — CSS color variable overrides
    ├── README.md       # Recommended — description and design notes
    └── preview.png     # Optional — screenshot for documentation
```

## theme.json Schema

```json
{
    "name": "My Theme",
    "description": "Short description of the theme",
    "author": "Your Name",
    "version": "1.0.0",
    "type": "dark"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name shown in the theme switcher |
| `description` | string | Brief description of the theme |
| `author` | string | Theme author or "Built-in" for included themes |
| `version` | string | Semantic version |
| `type` | string | `"dark"` or `"light"` — informational label |

All fields are required.

## CSS Color Variables

Override these variables in `colors.css` using a `:root` selector. You only need to override the variables you want to change — unset variables keep their Nord defaults.

### Background Colors

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--nord0` | `#2e3440` | Base background reference |
| `--nord1` | `#3b4252` | Card background reference |
| `--nord2` | `#434c5e` | Elevated/hover background reference |
| `--nord3` | `#4c566a` | Border reference |
| `--bg-primary` | `var(--nord0)` | Page background |
| `--bg-card` | `var(--nord1)` | Card backgrounds |
| `--bg-sidebar` | `var(--nord0)` | Sidebar background |
| `--bg-elevated` | `var(--nord2)` | Elevated surfaces |
| `--bg-input` | `var(--nord0)` | Input field backgrounds |
| `--bg-hover` | `var(--nord2)` | Hover state backgrounds |

### Text Colors

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--nord4` | `#d8dee9` | Secondary text reference |
| `--nord5` | `#e5e9f0` | Body text reference |
| `--nord6` | `#eceff4` | Primary text reference |
| `--text-primary` | `var(--nord6)` | Primary text |
| `--text-secondary` | `var(--nord4)` | Secondary/dimmer text |
| `--text-muted` | `#97abbe` | Muted/label text |
| `--text-inverse` | `var(--nord0)` | Text on accent backgrounds |

### Accent Colors

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--accent` | `#81a1c1` | Primary accent (buttons, links, active states) |
| `--accent-light` | `#88c0d0` | Light accent (highlights, ratings) |
| `--accent-teal` | `#8fbcbb` | Teal accent (supplementary) |

### Border Colors

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--border-default` | `var(--nord3)` | Standard borders |
| `--border-subtle` | `var(--nord2)` | Subtle/secondary borders |
| `--border-focus` | `var(--accent)` | Focus ring color |

### Semantic Colors

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--color-success` | `#a3be8c` | Success states (completed, unignore) |
| `--color-warning` | `#ebcb8b` | Warning states (unread badges) |
| `--color-error` | `#bf616a` | Error states (danger buttons, failures) |
| `--color-info` | `var(--accent)` | Info states (loading, sync) |

### Shadow Variables

| Variable | Default (Nord) | Description |
|----------|---------------|-------------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.3)` | Small shadow |
| `--shadow-md` | `0 2px 8px rgba(0,0,0,0.3)` | Medium shadow |
| `--shadow-lg` | `0 4px 16px rgba(0,0,0,0.4)` | Large shadow |

## How color-mix() Works

The stylesheet uses `color-mix()` to auto-derive transparent variants from your theme colors. For example:

```css
/* In style.css */
.badge-status {
    background: color-mix(in srgb, var(--color-success) 10%, transparent);
    border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
}
```

When you override `--color-success` in your theme, the badge backgrounds and borders automatically adjust. You do **not** need to define the transparent variants yourself.

## Step-by-Step: Creating a Custom Theme

1. **Create the theme directory:**
   ```bash
   mkdir src/web/static/themes/my-theme
   ```

2. **Create `theme.json`:**
   ```json
   {
       "name": "My Theme",
       "description": "A custom color scheme",
       "author": "Your Name",
       "version": "1.0.0",
       "type": "dark"
   }
   ```

3. **Create `colors.css`** with your color overrides:
   ```css
   :root {
       --accent: #e06c75;
       --accent-light: #e5c07b;
       --color-success: #98c379;
       --color-warning: #d19a66;
       --color-error: #be5046;
       /* ... override as many variables as needed */
   }
   ```

4. **Test your theme:**
   - Start the server
   - Select your theme from the sidebar dropdown
   - Check all pages: Recommendations, Library, Chat, Data, Preferences
   - Verify text readability, badge contrast, and button visibility

5. **Optionally add a README.md** describing your design choices.

## What Themes Cannot Override

Themes only affect **color** variables. The following are not theme-overridable:

- Spacing scale (`--space-*`)
- Typography (`--font-sans`, `--font-mono`, `--text-*`)
- Border radius (`--radius-*`)
- Transitions (`--transition-*`)
- Layout dimensions (`--sidebar-width`, etc.)

## Theme Persistence

- Users select a theme via the sidebar dropdown
- The selection is saved to `localStorage` per browser
- Server admins can set a default theme via `web.theme` in `config.yaml`
- `localStorage` takes priority over the config default
