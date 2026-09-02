# Snowstorm Theme

A clean light theme inspired by the Nord Snow Storm palette.

## Design

Inverts the default dark palette:

- **Backgrounds:** White and light grays
- **Text:** Dark navy and charcoal
- **Accents:** Deeper frost variants for contrast on white
- **Semantic:** Darkened greens, yellows, and reds for light-background readability

## Notes

`colors.css` carries the whole palette; `resources/css/base.css` holds only an
unbranded grey fallback of the same contract. Its `color-mix()` declarations
derive the transparent badge and button fills from what is set here, so those
need no CSS of their own.

It sets colour, and nothing else. Spacing, the type scale, `--rail-w` and
`--measure` are core and stay in `base.css` — see
[docs/THEME_DEVELOPMENT.md](../../../../../docs/THEME_DEVELOPMENT.md).
