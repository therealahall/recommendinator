# Snowstorm Theme

A clean light theme inspired by the Nord Snow Storm palette.

## Design

Inverts the standard Nord palette:

- **Backgrounds:** Snow Storm colors (white, light grays)
- **Text:** Polar Night colors (dark navy, charcoal)
- **Accents:** Deeper Frost variants for contrast on white
- **Semantic:** Aurora colors darkened for light-background readability

## Notes

This theme overrides all 27 core color variables. The `color-mix()` declarations
in `style.css` automatically derive transparent badge/button backgrounds from
these overridden values, so no additional CSS is needed.
