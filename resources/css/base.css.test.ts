import { readdirSync, readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import { contrastRatio, themeTokens, tokenContrast } from '@/testing/contrast'

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

describe('error text token', () => {
  it('derives error text from the active palette, not the fill colour', () => {
    // --color-error is sized for fills: 2.46:1 as text on --bg-card.
    const source = readBase()
    const match = source.match(/--color-error-text:([^;]*);/)
    if (!match) throw new Error('--color-error-text declaration not found in base.css')

    expect(match[1]).toContain('var(--color-error)')
    expect(match[1]).toContain('var(--text-primary)')
  })
})

describe('account & sign-in surface contrast', () => {
  // Measured, not quoted: --accent and --accent-light both sit under 4.5:1 as
  // text on a card, which is invisible until someone computes it.
  const THEMES: Record<string, Record<string, string>> = {
    Nord: themeTokens('resources/css/base.css'),
    Snowstorm: themeTokens(
      'resources/css/base.css',
      'src/web/static/themes/snowstorm/colors.css',
    ),
  }

  const TEXT_PAIRS: [string, string, string][] = [
    ['field labels', 'var(--text-primary)', 'var(--bg-card)'],
    ['field hints and status text', 'var(--text-secondary)', 'var(--bg-card)'],
    ['a refusal message', 'var(--color-error-text)', 'var(--bg-card)'],
    ['the sidebar user name', 'var(--text-primary)', 'var(--bg-sidebar)'],
    ['the sidebar "signed in as" label', 'var(--text-secondary)', 'var(--bg-sidebar)'],
  ]

  const CONTROL_PAIRS: [string, string, string][] = [
    ['the resting field border', 'var(--text-muted)', 'var(--bg-input)'],
    ['the focus ring', 'var(--accent)', 'var(--bg-input)'],
  ]

  it('measures every shipped theme, with the theme layered over the base', () => {
    expect(Object.keys(THEMES)).toEqual(['Nord', 'Snowstorm'])
    expect(THEMES.Nord['--bg-card']).toBe('#3b4252')
    expect(THEMES.Snowstorm['--bg-card']).toBe('#ffffff')
    // Snowstorm redeclares no spacing, so the base scale has to show through.
    expect(THEMES.Snowstorm['--space-4']).toBe('16px')
  })

  it('agrees with the reference ratio for black on white', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1)
  })

  it('measures every theme the app ships, not a list that stopped being one', () => {
    // A hand-written theme list passes forever while a third theme goes
    // unmeasured, so the population is read off disk and compared.
    const shipped = readdirSync(`${process.cwd()}/src/web/static/themes`, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()

    expect(shipped.length).toBeGreaterThan(0)
    expect(
      Object.keys(THEMES)
        .map((name) => name.toLowerCase())
        .sort(),
    ).toEqual(shipped)
  })

  for (const [theme, tokens] of Object.entries(THEMES)) {
    describe(theme, () => {
      it.each(TEXT_PAIRS)('clears 4.5:1 for %s', (_what, foreground, background) => {
        expect(tokenContrast(tokens, foreground, background)).toBeGreaterThanOrEqual(4.5)
      })

      it.each(CONTROL_PAIRS)('clears 3:1 for %s', (_what, foreground, background) => {
        expect(tokenContrast(tokens, foreground, background)).toBeGreaterThanOrEqual(3)
      })

      it('makes keyboard focus visible against the state it replaces', () => {
        // The resting edge above looks the same focused or not, so what a
        // sighted keyboard user has to see is the difference: the border swap
        // and the ring that `outline: none` leaves in place of the global one.
        const borderSwap = tokenContrast(tokens, 'var(--accent)', 'var(--text-muted)')
        const ringOnCard = tokenContrast(
          tokens,
          'color-mix(in srgb, var(--accent) 30%, var(--bg-card))',
          'var(--bg-card)',
        )

        expect(
          Math.max(borderSwap, ringOnCard),
          `border swap ${borderSwap.toFixed(2)}:1, ring ${ringOnCard.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3)
      })
    })
  }
})

describe('one-handed reach on a phone', () => {
  it('gives the sign-in submit a thumb-sized target', () => {
    const match = readBase().match(/\.auth-submit\s*\{([^}]*)\}/)
    if (!match) throw new Error('.auth-submit rule not found in base.css')

    expect(match[1]).toMatch(/min-height:\s*44px/)
  })

  it('sizes the standalone screens to the visible viewport, not the tallest one', () => {
    // 100vh alone strands the submit button under a collapsing mobile toolbar.
    const match = readBase().match(/\.auth-screen\s*\{([^}]*)\}/)
    if (!match) throw new Error('.auth-screen rule not found in base.css')

    expect(match[1]).toMatch(/min-height:\s*100dvh/)
  })
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
