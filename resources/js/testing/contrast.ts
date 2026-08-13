import { readFileSync } from 'node:fs'

/** WCAG 2.1 contrast, computed from the hex a theme actually declares. A ratio
 *  quoted in a comment is a ratio nobody re-checks when the palette moves. */

const HEX = /^#([0-9a-f]{6})$/i
const VAR = /^var\((--[a-z0-9-]+)\)$/
const MIX = /^color-mix\(in srgb,\s*(.+?)\s+([\d.]+)%,\s*(.+)\)$/
const DECLARATION = /(--[a-z0-9-]+):\s*([^;]+);/g
// Stripped first: the palette's comments quote token names, and a quoted
// `--token:` reads as a declaration whose value runs to the next semicolon.
const COMMENT = /\/\*[\s\S]*?\*\//g

function channels(hex: string): [number, number, number] {
  const match = HEX.exec(hex)
  if (!match) throw new Error(`not a six-digit hex colour: ${hex}`)
  const packed = parseInt(match[1], 16)
  return [(packed >> 16) & 0xff, (packed >> 8) & 0xff, packed & 0xff]
}

function linearize(value: number): number {
  const srgb = value / 255
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const [red, green, blue] = channels(hex)
  return 0.2126 * linearize(red) + 0.7152 * linearize(green) + 0.0722 * linearize(blue)
}

export function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** Every `--token: value` in the given files, later files overriding earlier
 *  ones — which is how a theme's colors.css is layered over base.css at run
 *  time, via a <link> that redeclares a subset of :root. */
export function themeTokens(...paths: string[]): Record<string, string> {
  const tokens: Record<string, string> = {}
  for (const path of paths) {
    const source = readFileSync(`${process.cwd()}/${path}`, 'utf8').replace(COMMENT, '')
    for (const [, name, value] of source.matchAll(DECLARATION)) {
      tokens[name] = value.trim()
    }
  }
  return tokens
}

function mix(foreground: string, background: string, weight: number): string {
  const front = channels(foreground)
  const back = channels(background)
  // `in srgb` mixes the gamma-encoded coordinates, so this is a plain
  // per-channel blend of the 0–255 values rather than of the linear ones.
  const blended = front.map((value, index) =>
    Math.round(value * weight + back[index] * (1 - weight)),
  )
  return `#${blended.map((value) => value.toString(16).padStart(2, '0')).join('')}`
}

/** Flattens a token's declared value to hex, following `var()` chains and the
 *  two-colour `color-mix(in srgb, …)` the palette uses for derived tokens. */
export function resolveColor(tokens: Record<string, string>, expression: string): string {
  const value = expression.trim()

  const reference = VAR.exec(value)
  if (reference) {
    const referenced = tokens[reference[1]]
    if (referenced === undefined) throw new Error(`undeclared token: ${reference[1]}`)
    return resolveColor(tokens, referenced)
  }

  const blend = MIX.exec(value)
  if (blend) {
    return mix(
      resolveColor(tokens, blend[1]),
      resolveColor(tokens, blend[3]),
      Number(blend[2]) / 100,
    )
  }

  if (!HEX.test(value)) throw new Error(`unsupported colour expression: ${value}`)
  return value
}

/** Contrast between two token expressions, resolved against one theme. */
export function tokenContrast(
  tokens: Record<string, string>,
  foreground: string,
  background: string,
): number {
  return contrastRatio(resolveColor(tokens, foreground), resolveColor(tokens, background))
}
