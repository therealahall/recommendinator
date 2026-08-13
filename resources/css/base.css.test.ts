import { readdirSync, readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import { contrastRatio, themeTokens, tokenContrast } from '@/testing/contrast'
import { globalFocusRing } from '@/testing/focusRing'
import { componentStyles } from '@/testing/styles'

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
  ]

  // Every surface an offset ring around a text field lands on. --bg-primary is
  // there because neither .auth-card nor .token-gate-card declares a
  // background: on the two standalone screens the ring reaches the body.
  const RING_SURFACES = ['--bg-card', '--bg-input', '--bg-primary']

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

  it('refuses a pair naming a token no theme declares', () => {
    // A resolver that defaulted a missing token would leave every ratio here
    // measuring a colour nobody ships, and the block would check nothing.
    expect(() => tokenContrast(THEMES.Nord, 'var(--nonexistent)', 'var(--bg-card)')).toThrow(
      /undeclared token/,
    )
    // Both positions: a background nobody declares would be as silent.
    expect(() => tokenContrast(THEMES.Nord, 'var(--accent-light)', 'var(--nonexistent)')).toThrow(
      /undeclared token/,
    )
  })

  it('refuses a surface the ring is measured against but no theme declares', () => {
    for (const [theme, tokens] of Object.entries(THEMES)) {
      for (const surface of RING_SURFACES) {
        expect(tokens[surface], `${theme} declares no ${surface}`).toMatch(/^#[0-9a-f]{6}$/i)
      }
    }
  })

  it('names the colours the shipped field is actually built from', () => {
    // The pairs are token names typed out by hand: this is what ties them to
    // the rule that renders, so restyling the edge fails here rather than
    // leaving a measurement of a border the field no longer has.
    const rule = componentStyles('resources/js/components/atoms/AuthField.vue').match(
      /\.auth-field input\s*\{([^}]*)\}/,
    )
    if (!rule) throw new Error('.auth-field input rule not found in AuthField.vue')

    expect(rule[1]).toMatch(/background:\s*var\(--bg-input\)/)
    expect(rule[1]).toMatch(/border:\s*1px solid var\(--text-muted\)/)
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

      it('makes keyboard focus visible on both surfaces the ring touches', () => {
        // Regression: the field drew its own ring instead — an --accent border
        // at 1.14:1 against the resting edge, tinted at 1.55:1 on the card.
        const ring = globalFocusRing()

        // Offset, so it sits in the surface behind the field rather than on
        // the field's own edge, and has to clear 3:1 against both (1.4.11).
        expect(ring.offsetPx).toBeGreaterThan(0)
        for (const surface of RING_SURFACES) {
          expect(
            tokenContrast(tokens, ring.color, `var(${surface})`),
            `${ring.color} on ${surface}`,
          ).toBeGreaterThanOrEqual(3)
        }
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
