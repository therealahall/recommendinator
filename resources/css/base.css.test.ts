import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import {
  contrastRatio,
  parseTokens,
  rendered,
  type BackgroundMode,
  type Tokens,
} from './contrast'

// base.css is a static asset: importing it through Vite yields an empty stub
// under Vitest, so read the file off disk to assert on its real contents.

// Isolate the `.sr-only { ... }` declaration block so the assertion cannot be
// satisfied by an unrelated rule that happens to mention user-select. This
// assumes `.sr-only` is a standalone selector; if it is ever merged into a
// multi-selector rule the regex won't match and the test throws "not found",
// which is the correct fail-mode.
function srOnlyBlock(source: string): string {
  const match = source.match(/\.sr-only\s*\{([^}]*)\}/)
  if (!match) throw new Error('.sr-only rule not found in base.css')
  return match[1]
}

function readBase(): string {
  return readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
}

describe('inactive button styling', () => {
  // Regression: the project had NO `.btn:disabled` rule at all. `.btn` sets a
  // solid background and `cursor: pointer`, so every button locked during a
  // save — Reset, Add, each chip's ×, Replace/Clear/Save secret, and every
  // toggle — rendered pixel-identically to a working one and silently ignored
  // clicks. Native inputs and selects get UA greying; buttons get none, so the
  // in-flight lock was conveyed to assistive tech and to nobody else (1.3.1).
  //
  // Asserted here rather than per component because the whole point is that one
  // shared rule covers every button; a per-component test would pass while the
  // next new button shipped unstyled.
  it('dims and re-cursors both inactive spellings', () => {
    const source = readBase()
    const match = source.match(
      /\.btn:disabled,\s*\.btn\[aria-disabled='true'\],\s*\.toggle-switch:disabled\s*\{([^}]*)\}/,
    )
    if (!match) throw new Error('shared inactive-button rule not found in base.css')

    expect(match[1]).toMatch(/opacity:/)
    expect(match[1]).toMatch(/cursor:\s*not-allowed/)
  })

  it('never brightens an inactive button on hover', () => {
    // aria-disabled buttons stay in the hover-able tree, so an ungated
    // `.btn-*:hover` lights them up while `cursor: not-allowed` says otherwise.
    const source = readBase()
    const hoverRules = source.match(/^\.btn-[a-z]+:hover[^{]*/gm) ?? []

    expect(hoverRules.length).toBeGreaterThan(0)
    for (const rule of hoverRules) {
      expect(rule, `${rule.trim()} is not gated on the inactive states`).toMatch(
        /:not\(:disabled\):not\(\[aria-disabled='true'\]\)/,
      )
    }
  })
})

// Every theme is base.css's :root with a colours.css layered on top, so a
// theme's effective palette is the merge. Nord is the default: its colours.css
// is a comment explaining that base.css already holds the palette, so there is
// no block to merge. Stated per theme rather than sniffed from the file, so a
// theme that moves its tokens somewhere this parser cannot see them fails
// loudly instead of quietly measuring base.css's palette a second time.
type PaletteMode = 'is-base' | 'overrides-base'

const THEMES: [string, PaletteMode][] = [
  ['nord', 'is-base'],
  ['snowstorm', 'overrides-base'],
]

function themeTokens(theme: string, palette: PaletteMode): Tokens {
  const base = parseTokens(readBase())
  const overrides = readFileSync(
    `${process.cwd()}/src/web/static/themes/${theme}/colors.css`,
    'utf8',
  )
  if (palette === 'is-base') {
    if (/:root\s*\{/.test(overrides)) {
      throw new Error(`${theme} now overrides the base palette`)
    }
    return base
  }
  return { ...base, ...parseTokens(overrides) }
}

// WCAG 1.4.3 Contrast (Minimum) for normal-size text. Every selector below is
// normal weight at --text-sm (13px) or larger, so none qualifies for the 3:1
// large-text allowance.
const AA_NORMAL_TEXT = 4.5

// Each entry is [selector, the surface the element sits on, whether the rule
// paints its own background]. The status banners tint that surface through a
// translucent background, so which surface it is changes the answer — all of
// them render inside a `.card` or the import dialog, both of which are
// --bg-card.
const CONTRAST_CASES: [string, string, BackgroundMode][] = [
  // Carries the import 413/oversize/parse-failure messages and the source
  // removal-failure alert.
  ['.sync-status-error', 'bg-card', 'own-background'],
  ['.sync-status-success', 'bg-card', 'own-background'],
  ['.sync-status-warning', 'bg-card', 'own-background'],
  // "Importing…" while an upload is in flight.
  ['.sync-status-info', 'bg-card', 'own-background'],
  // Required instructions, not decoration: the accepted file types and the
  // reason Import is disabled are both wired to a control via aria-describedby.
  // Unstyled background: the text is painted straight onto the surface.
  ['.help-text', 'bg-card', 'inherits'],
  // Remove, on every leftover file-import row.
  ['.btn-danger', 'bg-card', 'own-background'],
]

describe('WCAG AA text contrast of shared colour rules', () => {
  // Regression: these ratios were computed from the committed hex values and
  // found short — .sync-status-error at 2.27:1 and .sync-status-info at 3.24:1
  // in nord, .help-text at 3.58:1 in snowstorm, and .btn-danger at 1.53:1 in
  // snowstorm, i.e. a destructive button whose label was effectively invisible.
  // Asserted against the parsed stylesheet rather than hardcoded numbers so
  // that editing a token, a tint percentage, or a colour re-runs the maths.
  for (const [theme, palette] of THEMES) {
    it.each(CONTRAST_CASES)(
      `${theme}: %s on %s clears 4.5:1`,
      (selector, surface, mode) => {
        const { text, background } = rendered(
          readBase(),
          themeTokens(theme, palette),
          selector,
          surface,
          mode,
        )
        expect(contrastRatio(text, background)).toBeGreaterThanOrEqual(
          AA_NORMAL_TEXT,
        )
      },
    )
  }
})

describe('.sr-only utility', () => {
  it('disables text selection so hidden labels never enter a copy', () => {
    // Browsers pull visually-clipped text into a selection, so copying an
    // on-screen value next to an sr-only label would paste the hidden words.
    // `user-select: none` is the root-level guard against that defect.
    //
    // Require BOTH the standard and the `-webkit-` declarations: the standard
    // one covers Chrome/Firefox, the prefixed one covers Safari. A loose
    // /user-select:\s*none/ would match the `-webkit-` line as a substring and
    // so pass even if the unprefixed declaration were dropped, so assert each
    // explicitly. The negative lookbehind isolates the unprefixed declaration.
    const source = readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
    const block = srOnlyBlock(source)
    expect(block).toMatch(/-webkit-user-select:\s*none/)
    expect(block).toMatch(/(?<!-)user-select:\s*none/)
  })
})
