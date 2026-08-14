import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// A profile tag's text is a genre name, so nothing stands in for it and it owes
// 4.5:1 (WCAG 1.4.3). Measured in both themes, because a theme overrides only
// tokens and a token move is what breaks this silently.

const AA_NORMAL_TEXT = 4.5

const THEMES: [string, string][] = [
  ['Nord', 'src/web/static/themes/nord/colors.css'],
  ['Snowstorm', 'src/web/static/themes/snowstorm/colors.css'],
]

/** The ratio each tag reaches on the Preferences card, per theme. */
const MEASURED: Record<string, Record<string, number>> = {
  Nord: { '.profile-tag': 7.03, '.profile-tag.anti': 4.82 },
  Snowstorm: { '.profile-tag': 10.52, '.profile-tag.anti': 8.54 },
}

/** What the tags carried before, and what each reached on the same card. */
const REJECTED: Record<string, Record<string, [string, string]>> = {
  Nord: {
    '4.06': ['var(--accent-light)', 'color-mix(in srgb, var(--accent) 15%, transparent)'],
    '2.28': ['var(--color-error)', 'color-mix(in srgb, var(--color-error) 10%, transparent)'],
  },
  Snowstorm: {
    '3.00': ['var(--accent-light)', 'color-mix(in srgb, var(--accent) 15%, transparent)'],
  },
}

interface Rgba {
  r: number
  g: number
  b: number
  a: number
}

const TRANSPARENT: Rgba = { r: 0, g: 0, b: 0, a: 0 }

function read(relativePath: string): string {
  return readFileSync(`${process.cwd()}/${relativePath}`, 'utf8')
}

function customProperties(source: string): [string, string][] {
  const root = source.match(/:root\s*\{([\s\S]*?)\n\}/)
  return [...(root?.[1] ?? '').matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [
    name,
    value.trim(),
  ])
}

function ruleBody(source: string, selector: string): string {
  const pattern = new RegExp(`^${selector.replace(/\./g, '\\.')}\\s*\\{([^}]*)\\}`, 'm')
  const match = source.match(pattern)
  if (!match) throw new Error(`${selector} rule not found`)
  return match[1]
}

function declaration(body: string, property: string): string {
  const match = body.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))
  if (!match) throw new Error(`no ${property} declared`)
  return match[1].trim()
}

/** Splits on the commas that are not inside a nested function call. */
function commaSeparated(text: string): string[] {
  const parts: string[] = []
  let depth = 0
  let start = 0
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === '(') depth += 1
    else if (text[i] === ')') depth -= 1
    else if (text[i] === ',' && depth === 0) {
      parts.push(text.slice(start, i))
      start = i + 1
    }
  }
  parts.push(text.slice(start))
  return parts.map((part) => part.trim())
}

/** color-mix interpolates premultiplied, so a transparent share tints nothing. */
function mixSrgb(first: Rgba, share: number, second: Rgba): Rgba {
  const rest = 1 - share
  const a = first.a * share + second.a * rest
  if (a === 0) return TRANSPARENT
  const blend = (one: number, two: number): number =>
    (one * first.a * share + two * second.a * rest) / a
  return { r: blend(first.r, second.r), g: blend(first.g, second.g), b: blend(first.b, second.b), a }
}

function toRgba(value: string, vars: Map<string, string>): Rgba {
  const text = value.trim()
  if (text === 'transparent') return TRANSPARENT

  const hex = text.match(/^#([0-9a-f]{6})$/i)
  if (hex) {
    const packed = parseInt(hex[1], 16)
    return { r: (packed >> 16) & 255, g: (packed >> 8) & 255, b: packed & 255, a: 1 }
  }

  const variable = text.match(/^var\((--[\w-]+)\)$/)
  if (variable) {
    const declared = vars.get(variable[1])
    if (declared === undefined) throw new Error(`${variable[1]} is declared nowhere`)
    return toRgba(declared, vars)
  }

  const mix = text.match(/^color-mix\((.*)\)$/)
  if (!mix) throw new Error(`unsupported colour: ${text}`)
  const [space, first, second] = commaSeparated(mix[1])
  if (space !== 'in srgb') throw new Error(`unsupported mix space: ${space}`)
  const share = first.match(/\s(\d+)%$/)
  if (!share) throw new Error(`expected a percentage on the first argument: ${first}`)
  return mixSrgb(toRgba(first.slice(0, share.index), vars), Number(share[1]) / 100, toRgba(second, vars))
}

function over(top: Rgba, backdrop: Rgba): Rgba {
  const blend = (one: number, two: number): number => one * top.a + two * (1 - top.a)
  return {
    r: blend(top.r, backdrop.r),
    g: blend(top.g, backdrop.g),
    b: blend(top.b, backdrop.b),
    a: 1,
  }
}

function luminance(colour: Rgba): number {
  const channel = (value: number): number => {
    const scaled = value / 255
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b)
}

function contrast(text: Rgba, backdrop: Rgba): number {
  const [lighter, darker] = [luminance(over(text, backdrop)), luminance(backdrop)].sort(
    (one, two) => two - one,
  )
  return (lighter + 0.05) / (darker + 0.05)
}

describe.each(THEMES)('profile tags on the Preferences card in %s', (theme, themePath) => {
  const base = read('resources/css/base.css')
  const vars = new Map([...customProperties(base), ...customProperties(read(themePath))])
  const card = toRgba('var(--bg-card)', vars)

  const ratio = (text: string, fill: string): number =>
    contrast(toRgba(text, vars), over(toRgba(fill, vars), card))

  it.each(Object.keys(MEASURED[theme]))('%s carries readable text', (selector) => {
    const body = ruleBody(base, selector)
    const measured = ratio(declaration(body, 'color'), declaration(body, 'background'))

    expect(measured).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
    expect(measured, 'the recorded ratio is stale').toBeCloseTo(MEASURED[theme][selector], 1)
  })

  it.each(Object.entries(REJECTED[theme]))(
    'rejects the %s:1 pairing it used to carry',
    (expected, [text, fill]) => {
      // Without this the checker could return anything and still pass above.
      const measured = ratio(text, fill)

      expect(measured).toBeCloseTo(Number(expected), 1)
      expect(measured).toBeLessThan(AA_NORMAL_TEXT)
    },
  )
})
