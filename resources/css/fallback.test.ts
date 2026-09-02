import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

// contrast.test.ts measures the installed themes. This measures what is left
// when a colors.css declares none of a token: the unbranded greys in base.css,
// which a theme with one line in it still has to be readable on.

const AA_NORMAL_TEXT = 4.5
const NON_TEXT = 3
const BASE = 'resources/css/base.css'

interface Rgb {
  r: number
  g: number
  b: number
}

function fallbackTokens(): Map<string, string> {
  const source = readFileSync(resolve(process.cwd(), BASE), 'utf8')
  const root = source.match(/:root\s*\{([\s\S]*?)\n\s*\}/)
  if (!root) throw new Error(`no :root block in ${BASE}`)
  return new Map(
    [...root[1].matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [
      name,
      value.trim(),
    ]),
  )
}

const VARS = fallbackTokens()

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

function toRgb(value: string): Rgb {
  const text = value.trim()

  const hex = text.match(/^#([0-9a-f]{6})$/i)
  if (hex) {
    const packed = parseInt(hex[1], 16)
    return { r: (packed >> 16) & 255, g: (packed >> 8) & 255, b: packed & 255 }
  }

  const variable = text.match(/^var\((--[\w-]+)\)$/)
  if (variable) {
    const declared = VARS.get(variable[1])
    if (declared === undefined) throw new Error(`${variable[1]} is declared nowhere`)
    return toRgb(declared)
  }

  const mix = text.match(/^color-mix\((.*)\)$/)
  if (!mix) throw new Error(`unsupported colour: ${text}`)
  const [space, first, second] = commaSeparated(mix[1])
  if (space !== 'in srgb') throw new Error(`unsupported mix space: ${space}`)
  const share = first.match(/\s(\d+)%$/)
  if (!share) throw new Error(`expected a percentage on the first argument: ${first}`)
  const weight = Number(share[1]) / 100
  const one = toRgb(first.slice(0, share.index))
  const two = toRgb(second)
  const blend = (a: number, b: number): number => a * weight + b * (1 - weight)
  return { r: blend(one.r, two.r), g: blend(one.g, two.g), b: blend(one.b, two.b) }
}

function luminance(colour: Rgb): number {
  const channel = (value: number): number => {
    const scaled = value / 255
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b)
}

function ratio(token: string, surface: string): number {
  const [lighter, darker] = [luminance(toRgb(`var(${token})`)), luminance(toRgb(`var(${surface})`))].sort(
    (one, two) => two - one,
  )
  return (lighter + 0.05) / (darker + 0.05)
}

const SURFACES = [
  '--bg-primary',
  '--bg-card',
  '--bg-sidebar',
  '--bg-elevated',
  '--bg-input',
  '--chrome',
]
const EDGE_SURFACES = [...SURFACES, '--bg-hover']
const TEXT = ['--text-primary', '--text-secondary', '--text-muted']
const SEMANTIC_TEXT = [
  '--color-error-text',
  '--color-info-text',
  '--color-success-text',
  '--color-warning-text',
]

describe('the unbranded fallback a theme degrades to', () => {
  it('is achromatic, so it can never read as somebody’s palette', () => {
    const branded = [...VARS]
      .filter(([, value]) => /^#[0-9a-f]{6}$/i.test(value))
      .filter(([, value]) => new Set([value.slice(1, 3), value.slice(3, 5), value.slice(5)]).size > 1)

    expect(branded).toEqual([])
  })

  it.each(TEXT.flatMap((token) => SURFACES.map((surface): [string, string] => [token, surface])))(
    '%s is readable on %s',
    (token, surface) => {
      expect(ratio(token, surface)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
    },
  )

  it.each(SEMANTIC_TEXT)('%s is readable on the card that carries it', (token) => {
    expect(ratio(token, '--bg-card')).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  it.each(EDGE_SURFACES)('--border-interactive divides a control from %s', (surface) => {
    expect(ratio('--border-interactive', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(EDGE_SURFACES)('--border-default divides a control from %s', (surface) => {
    expect(ratio('--border-default', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(SURFACES)('--accent-light rings a focused control on %s', (surface) => {
    expect(ratio('--accent-light', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(['--accent', '--accent-light'])('%s carries a readable label', (fill) => {
    expect(ratio('--text-inverse', fill)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})
