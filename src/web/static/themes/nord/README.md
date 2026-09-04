# Nord Theme

The default dark theme based on the [Nord color palette](https://www.nordtheme.com/).

## Colors

- **Backgrounds:** Dark navy and charcoal
- **Text:** Light snow-white tones
- **Accents:** Frost blues, the lighter one taking hover fills and the focus ring
- **Semantic:** Aurora greens, yellows, and reds

## Notes

`colors.css` carries the whole palette. `resources/css/base.css` holds only an
unbranded grey fallback of the same contract, so nothing here is inherited from
another theme, and dropping a variable paints grey rather than Nord.

It sets colour, and nothing else. Spacing, the type scale, `--rail-w` and
`--measure` are core and stay in `base.css` — see
[docs/THEME_DEVELOPMENT.md](../../../../../docs/THEME_DEVELOPMENT.md).
