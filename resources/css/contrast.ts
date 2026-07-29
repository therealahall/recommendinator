/**
 * WCAG contrast maths plus the sliver of CSS colour resolution the design
 * tokens actually use. Test-only: it exists so `base.css.test.ts` can assert
 * ratios against the committed hex values instead of numbers somebody computed
 * by hand once and never re-checked.
 */

export interface Rgb {
  r: number
  g: number
  b: number
}

/** A theme's `:root` custom properties, keyed WITHOUT the leading `--`. */
export type Tokens = Record<string, string>

export function parseHex(hex: string): Rgb {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) throw new Error(`not a 6-digit hex colour: ${hex}`)
  const value = parseInt(match[1], 16)
  return { r: (value >> 16) & 0xff, g: (value >> 8) & 0xff, b: value & 0xff }
}

/** `color-mix(in srgb, a <percent>%, b)` for two opaque colours. */
export function mix(a: Rgb, percent: number, b: Rgb): Rgb {
  const weight = percent / 100
  return {
    r: a.r * weight + b.r * (1 - weight),
    g: a.g * weight + b.g * (1 - weight),
    b: a.b * weight + b.b * (1 - weight),
  }
}

// WCAG 2.1 relative luminance (https://www.w3.org/TR/WCAG21/#dfn-relative-luminance).
function relativeLuminance({ r, g, b }: Rgb): number {
  const linear = [r, g, b].map((channel) => {
    const srgb = channel / 255
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

/** WCAG 2.1 contrast ratio, 1..21, order-independent. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [darker, lighter] = [relativeLuminance(a), relativeLuminance(b)].sort(
    (x, y) => x - y,
  )
  return (lighter + 0.05) / (darker + 0.05)
}

/** Parse the `--name: value;` declarations out of a `:root { ... }` block. */
export function parseTokens(css: string): Tokens {
  const root = /:root\s*\{([^}]*)\}/.exec(css)
  if (!root) throw new Error(':root block not found')
  const tokens: Tokens = {}
  for (const [, name, value] of root[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[name] = value.trim()
  }
  return tokens
}

/**
 * Isolate one declaration from one rule, e.g. `color` of `.help-text`.
 *
 * More than one rule for the same selector is a hard error rather than a
 * first-or-last guess: the browser paints the last one, but only after applying
 * a cascade this parser does not model, so any answer here would be a guess
 * that reads like a fact.
 */
export function declaration(
  css: string,
  selector: string,
  property: string,
): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const rule = new RegExp(`(?:^|[,}/])\\s*${escaped}\\s*\\{([^}]*)\\}`, 'gm')
  const blocks = [...css.matchAll(rule)]
  if (blocks.length === 0) throw new Error(`rule not found: ${selector}`)
  if (blocks.length > 1) {
    throw new Error(
      `${selector} has ${blocks.length} rules; this parser models one`,
    )
  }
  const found = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`).exec(
    blocks[0][1],
  )
  if (!found) throw new Error(`${selector} declares no ${property}`)
  return found[1].trim()
}

/**
 * Resolve a colour expression to what the browser paints.
 *
 * Covers the grammar the design tokens use and nothing more: a hex literal,
 * `var(--token)` (recursively), and `color-mix(in srgb, <expr> N%, <expr>)`.
 * An unsupported expression throws rather than guessing, so a future rule
 * written in a richer syntax fails loudly instead of being silently skipped.
 *
 * `transparent` as the second mix argument is what the tint rules use, which
 * makes the result translucent — it is composited over `backdrop`, the surface
 * the element sits on.
 */
export function resolveColor(
  expression: string,
  tokens: Tokens,
  backdrop: Rgb,
): Rgb {
  const expr = expression.trim()

  const variable = /^var\(\s*--([\w-]+)\s*\)$/.exec(expr)
  if (variable) {
    const value = tokens[variable[1]]
    if (value === undefined) throw new Error(`undefined token: --${variable[1]}`)
    return resolveColor(value, tokens, backdrop)
  }

  const colorMix = /^color-mix\(\s*in srgb\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*(.+)\)$/.exec(
    expr,
  )
  if (colorMix) {
    const [, first, percent, second] = colorMix
    const base = resolveColor(first, tokens, backdrop)
    const other =
      second.trim() === 'transparent'
        ? backdrop
        : resolveColor(second, tokens, backdrop)
    return mix(base, Number(percent), other)
  }

  if (expr === 'black') return { r: 0, g: 0, b: 0 }
  if (expr === 'white') return { r: 255, g: 255, b: 255 }
  return parseHex(expr)
}

/**
 * Whether a rule paints its own `background` or lets the surface show through.
 * Declared per rule rather than inferred from a failed lookup, so that a rule
 * that grows an unresolvable background still fails loudly.
 */
export type BackgroundMode = 'own-background' | 'inherits'

/** What `selector`'s text and the background behind it resolve to. */
export function rendered(
  css: string,
  tokens: Tokens,
  selector: string,
  surfaceToken: string,
  mode: BackgroundMode,
): { text: Rgb; background: Rgb } {
  const surface = resolveColor(`var(--${surfaceToken})`, tokens, {
    r: 0,
    g: 0,
    b: 0,
  })
  const background =
    mode === 'own-background'
      ? resolveColor(declaration(css, selector, 'background'), tokens, surface)
      : surface
  return {
    text: resolveColor(declaration(css, selector, 'color'), tokens, background),
    background,
  }
}
