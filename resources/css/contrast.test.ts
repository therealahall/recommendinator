import { describe, it, expect } from 'vitest'
import {
  contrastRatio,
  declaration,
  mix,
  parseHex,
  parseTokens,
  rendered,
  resolveColor,
} from './contrast'

const WHITE = { r: 255, g: 255, b: 255 }
const BLACK = { r: 0, g: 0, b: 0 }

describe('parseHex', () => {
  it('splits a 6-digit hex into channels', () => {
    expect(parseHex('#3b4252')).toEqual({ r: 0x3b, g: 0x42, b: 0x52 })
  })

  it('rejects anything that is not a 6-digit hex', () => {
    expect(() => parseHex('#fff')).toThrow(/6-digit hex/)
  })
})

describe('contrastRatio', () => {
  // The two fixed points of WCAG 2.1: the maximum is black on white, and any
  // colour against itself is 1:1.
  it('gives 21:1 for black on white, either way round', () => {
    expect(contrastRatio(BLACK, WHITE)).toBeCloseTo(21, 5)
    expect(contrastRatio(WHITE, BLACK)).toBeCloseTo(21, 5)
  })

  it('gives 1:1 for a colour against itself', () => {
    expect(contrastRatio(parseHex('#bf616a'), parseHex('#bf616a'))).toBe(1)
  })

  // #767676 is the canonical "smallest grey that passes AA on white" used in
  // every WCAG contrast tutorial, so it pins the luminance curve, not just the
  // endpoints: a linear (un-gamma-corrected) implementation returns ~3.9 here.
  it('matches the published ratio for #767676 on white', () => {
    expect(contrastRatio(parseHex('#767676'), WHITE)).toBeCloseTo(4.54, 2)
  })
})

describe('mix', () => {
  it('weights the first colour by the percentage', () => {
    expect(mix(WHITE, 25, BLACK)).toEqual({ r: 63.75, g: 63.75, b: 63.75 })
  })
})

describe('parseTokens', () => {
  it('reads the custom properties out of a :root block', () => {
    const tokens = parseTokens(':root {\n  --bg-card: #3b4252;\n  --accent: var(--x);\n}')
    expect(tokens).toEqual({ 'bg-card': '#3b4252', accent: 'var(--x)' })
  })

  it('throws when there is no :root block to read', () => {
    expect(() => parseTokens('.card { color: red; }')).toThrow(/:root/)
  })
})

describe('declaration', () => {
  const css = '.btn-danger {\n  color: var(--text-on-danger);\n}\n\n' +
    '.btn-danger:hover {\n  color: red;\n}\n'

  it('reads a property from the rule with the exact selector', () => {
    expect(declaration(css, '.btn-danger', 'color')).toBe(
      'var(--text-on-danger)',
    )
  })

  it('throws rather than guessing when the rule is gone', () => {
    expect(() => declaration(css, '.btn-primary', 'color')).toThrow(
      /rule not found/,
    )
  })

  it('throws rather than guessing when the property is gone', () => {
    expect(() => declaration(css, '.btn-danger', 'background')).toThrow(
      /declares no background/,
    )
  })

  // Regression: the lookup used a non-global `RegExp.exec`, so it returned the
  // FIRST rule for the selector. CSS paints the last one, so appending an
  // override — the ordinary way to patch a colour — left the harness asserting
  // a value the browser never paints, and passing.
  it('refuses to pick a winner when the selector has two rules', () => {
    const overridden =
      '.sync-status-error {\n  color: var(--color-error);\n}\n\n' +
      '.sync-status-error {\n  color: var(--text-primary);\n}\n'

    expect(() => declaration(overridden, '.sync-status-error', 'color')).toThrow(
      /\.sync-status-error has 2 rules/,
    )
  })
})

describe('rendered', () => {
  const tokens = {
    'bg-card': '#3b4252',
    'color-error': '#bf616a',
    'text-primary': '#eceff4',
  }

  it('composites an own tinted background over the surface', () => {
    const css =
      '.sync-status-error {\n' +
      '  background: color-mix(in srgb, var(--color-error) 10%, transparent);\n' +
      '  color: var(--text-primary);\n' +
      '}\n'

    const { background } = rendered(
      css,
      tokens,
      '.sync-status-error',
      'bg-card',
      'own-background',
    )
    expect(background.r).toBeCloseTo(72.2, 6)
    expect(background.g).toBeCloseTo(69.1, 6)
    expect(background.b).toBeCloseTo(84.4, 6)
  })

  it('paints an inheriting rule straight onto the surface', () => {
    const css = '.help-text {\n  color: var(--text-primary);\n}\n'

    const { background } = rendered(
      css,
      tokens,
      '.help-text',
      'bg-card',
      'inherits',
    )
    expect(background).toEqual(parseHex(tokens['bg-card']))
  })

  // Regression: the background lookup was wrapped in a bare `catch` meant only
  // for rules with no background of their own. It also swallowed resolveColor
  // throwing, so restating a tint in a syntax the parser does not model —
  // `rgba()`, `oklch()` — silently substituted the untinted surface and went on
  // asserting a ratio the browser never paints.
  it('propagates an unmodelled background instead of falling back', () => {
    const css =
      '.sync-status-error {\n' +
      '  background: rgba(191, 97, 106, 0.1);\n' +
      '  color: var(--text-primary);\n' +
      '}\n'

    expect(() =>
      rendered(css, tokens, '.sync-status-error', 'bg-card', 'own-background'),
    ).toThrow(/6-digit hex/)
  })

  // The same hole one property over: an own-background rule that loses its
  // `background` declaration must not quietly measure against the surface.
  it('propagates a missing background on an own-background rule', () => {
    const css = '.sync-status-error {\n  color: var(--text-primary);\n}\n'

    expect(() =>
      rendered(css, tokens, '.sync-status-error', 'bg-card', 'own-background'),
    ).toThrow(/declares no background/)
  })
})

describe('resolveColor', () => {
  const tokens = {
    'bg-card': '#3b4252',
    'color-error': '#bf616a',
    'text-primary': '#eceff4',
    'border-focus': 'var(--color-error)',
  }

  it('follows a var() chain to its hex', () => {
    expect(resolveColor('var(--border-focus)', tokens, WHITE)).toEqual({
      r: 0xbf,
      g: 0x61,
      b: 0x6a,
    })
  })

  it('mixes two opaque colours', () => {
    expect(
      resolveColor(
        'color-mix(in srgb, var(--color-error) 50%, var(--text-primary))',
        tokens,
        WHITE,
      ),
    ).toEqual({ r: 213.5, g: 168, b: 175 })
  })

  it('composites a mix with transparent over the backdrop', () => {
    // `color-mix(..., transparent)` yields a translucent colour, so what the
    // eye sees depends on the surface underneath — the whole reason the tinted
    // status banners were failing contrast in the first place.
    const backdrop = parseHex(tokens['bg-card'])
    const composited = resolveColor(
      'color-mix(in srgb, var(--color-error) 10%, transparent)',
      tokens,
      backdrop,
    )
    expect(composited.r).toBeCloseTo(72.2, 6)
    expect(composited.g).toBeCloseTo(69.1, 6)
    expect(composited.b).toBeCloseTo(84.4, 6)
  })

  it('throws on an undefined token instead of assuming a colour', () => {
    expect(() => resolveColor('var(--nope)', tokens, WHITE)).toThrow(
      /undefined token/,
    )
  })

  it('throws on a colour syntax it does not model', () => {
    expect(() => resolveColor('rgba(0, 0, 0, 0.55)', tokens, WHITE)).toThrow(
      /6-digit hex/,
    )
  })
})
