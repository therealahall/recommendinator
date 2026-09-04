# GitHub Dark Dimmed Theme

A dark theme built on GitHub's [Primer](https://primer.style/) `dark_dimmed` palette.

## Design

- **Backgrounds:** Primer's canvas scale — an inset rail, a dimmed slate page, overlay cards
- **Text:** `fg.default` for body copy, the grey ramp below it for everything quieter
- **Accents:** Primer blues, with the lighter step taking hover fills and the focus ring
- **Semantic:** The published success green, attention amber and danger red

## Where it leaves Primer

Every canvas value and `--text-primary` is the published one. Three tokens step off the semantic scale:

- **`--text-muted`** takes `gray-2` `#909dab`. `fg.muted` `#768390` reaches 3.88:1 on the page, short of the 4.5:1 muted help text owes on every surface (WCAG 1.4.3).
- **`--color-error`** takes `red-3` `#f47067`. Error text is half of it mixed into `--text-primary`, and from `danger.fg` `#e5534b` that mix lands at 4.42:1 on a card.
- **`--accent`** takes `accent.fg` `#539bf5` rather than the `accent.emphasis` `#316dca` a Primer button fills with, and its label is dark rather than `fg.onEmphasis`. It fills the score bars and spine segments, which owe 3:1 against the track and card behind them, while `--focus-ring` derives from `--accent-hover` and owes the same on every surface it rings (WCAG 1.4.11). That pins each accent light, and one inverse colour owes both fills 4.5:1.

`--text-secondary` repeats `fg.default` rather than dimming: Primer publishes no grey between it and `gray-2`, and `gray-2` misses 4.5:1 on `--bg-hover`.

`canvas.overlay` and `canvas.subtle` are the same value here, so an overlay is bounded by its edge rather than by a fill step.

## Notes

`colors.css` carries the whole palette. `resources/css/base.css` holds only an
unbranded grey fallback of the same contract, so nothing here is inherited from
another theme, and dropping a variable paints grey rather than Primer.

It sets colour, and nothing else. Spacing, the type scale, `--rail-w` and
`--measure` are core and stay in `base.css` — see
[docs/THEME_DEVELOPMENT.md](../../../../../docs/THEME_DEVELOPMENT.md).
