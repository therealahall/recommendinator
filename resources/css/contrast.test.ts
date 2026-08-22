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

const TAGS = ['.profile-tag', '.profile-tag.anti']

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

const DROP_ZONE = 'resources/js/components/atoms/FileDropZone.vue'
const RESULT_SUMMARY = 'resources/js/components/molecules/ImportResultSummary.vue'
const IMPORT_PANEL = 'resources/js/components/organisms/ImportPanel.vue'

/** The import panel's own surfaces, none of them the plain card: text, the rule
 *  that colours it, and the rule declaring what it sits on. */
const IMPORT_SURFACES: [string, string, string, string][] = [
  ['the refusal notice', IMPORT_PANEL, '.import-error', '.import-error'],
  ['a count', RESULT_SUMMARY, '.import-count dd', '.import-counts'],
  ['a count label', RESULT_SUMMARY, '.import-count dt', '.import-counts'],
  ['a skipped line', RESULT_SUMMARY, '.import-misses-list', '.import-callout'],
  ['the misses heading', RESULT_SUMMARY, '.import-misses-title', '.import-callout'],
  ['a file note', RESULT_SUMMARY, '.import-note', '.import-callout'],
  ['the drop hint', DROP_ZONE, '.drop-zone-hint', '.drop-zone'],
  ['the chosen file', DROP_ZONE, '.drop-zone-selection', '.drop-zone'],
  ['the file label', DROP_ZONE, '.drop-zone-label', '.drop-zone'],
]

const DRAGGED_OVER = ['.drop-zone-hint', '.drop-zone-selection']

describe.each(THEMES)('import panel surfaces in %s', (_theme, themePath) => {
  const base = read('resources/css/base.css')
  const vars = new Map([...customProperties(base), ...customProperties(read(themePath))])
  const card = toRgba('var(--bg-card)', vars)

  it.each(IMPORT_SURFACES)(
    '%s carries readable text',
    (_label, componentPath, textSelector, surfaceSelector) => {
      const component = read(componentPath)
      const text = declaration(ruleBody(component, textSelector), 'color')
      const surface = declaration(ruleBody(component, surfaceSelector), 'background')

      expect(
        contrast(toRgba(text, vars), over(toRgba(surface, vars), card)),
      ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
    },
  )

  // .drop-zone-over sets background on the same element as .drop-zone, at equal
  // specificity, so --bg-active replaces --bg-elevated instead of sitting on it.
  it.each(DRAGGED_OVER)('%s stays readable with a file over the zone', (textSelector) => {
    const component = read(DROP_ZONE)
    const background = (selector: string): string =>
      declaration(ruleBody(component, selector), 'background')
    const dragged = over(toRgba(background('.drop-zone-over'), vars), card)
    const text = declaration(ruleBody(component, textSelector), 'color')

    expect(contrast(toRgba(text, vars), dragged)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})

describe.each(THEMES)('profile tags on the Preferences card in %s', (theme, themePath) => {
  const base = read('resources/css/base.css')
  const vars = new Map([...customProperties(base), ...customProperties(read(themePath))])
  const card = toRgba('var(--bg-card)', vars)

  const ratio = (text: string, fill: string): number =>
    contrast(toRgba(text, vars), over(toRgba(fill, vars), card))

  it.each(TAGS)('%s carries readable text', (selector) => {
    const body = ruleBody(base, selector)

    expect(ratio(declaration(body, 'color'), declaration(body, 'background'))).toBeGreaterThanOrEqual(
      AA_NORMAL_TEXT,
    )
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

const BASE = 'resources/css/base.css'
const LIBRARY_FILTERS = 'resources/js/components/organisms/LibraryFilters.vue'
const DISCARD_CONFIRM = 'resources/js/components/molecules/DiscardConfirm.vue'

/** Text and the rule painting what it sits on, which for two of the three is
 *  declared in another file. */
const EDIT_SURFACES: [string, string, string, string, string][] = [
  ['the export scope', LIBRARY_FILTERS, '.export-scope', BASE, '.dropdown-menu'],
  ['an enrichment note', BASE, '.edit-modal-note', BASE, '.edit-modal-content'],
  ['the discard question', DISCARD_CONFIRM, '.discard-confirm', DISCARD_CONFIRM, '.discard-confirm'],
]

describe.each(THEMES)('edit dialog surfaces in %s', (_theme, themePath) => {
  const vars = new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])

  it.each(EDIT_SURFACES)(
    '%s carries readable text',
    (_label, textPath, textSelector, surfacePath, surfaceSelector) => {
      const text = declaration(ruleBody(read(textPath), textSelector), 'color')
      const surface = declaration(ruleBody(read(surfacePath), surfaceSelector), 'background')

      expect(contrast(toRgba(text, vars), toRgba(surface, vars))).toBeGreaterThanOrEqual(
        AA_NORMAL_TEXT,
      )
    },
  )
})

const DUP_PAIR = 'resources/js/components/molecules/DuplicatePair.vue'
const DUP_HISTORY = 'resources/js/components/organisms/DuplicateHistory.vue'
const DUP_QUEUE = 'resources/js/components/organisms/DuplicateQueue.vue'
const DUP_PAGE = 'resources/js/components/pages/DuplicatesPage.vue'

/** A pair card sits on the plain card and its two rows sit on the pair card,
 *  so the backdrops stack: measuring against the card alone measures wrong. */
const DUPLICATE_SURFACES: [string, string, string, string[]][] = [
  ['the save-door-key badge', DUP_PAIR, '.dup-badge-exact', ['.dup-badge-exact', '.dup-pair']],
  ['the looser-key badge', DUP_PAIR, '.dup-badge-loose', ['.dup-badge-loose', '.dup-pair']],
  ['the content-type badge', DUP_PAIR, '.dup-badge-type', ['.dup-badge-type', '.dup-pair']],
  ['a copy offered twice', DUP_PAIR, '.dup-side-elsewhere', ['.dup-side', '.dup-pair']],
  ['the looser-key caution', DUP_PAIR, '.dup-pair-caution', ['.dup-pair']],
  ['a row title', DUP_PAIR, '.dup-side-title', ['.dup-side', '.dup-pair']],
  ['a row provenance', DUP_PAIR, '.dup-side-meta', ['.dup-side', '.dup-pair']],
  ['a merge details', DUP_HISTORY, '.dup-log-meta', ['.dup-log-row']],
  ['the reason a control refused', DUP_HISTORY, '.dup-log-reason', ['.dup-log-row']],
]

describe.each(THEMES)('duplicates review surfaces in %s', (_theme, themePath) => {
  const base = read('resources/css/base.css')
  const vars = new Map([...customProperties(base), ...customProperties(read(themePath))])
  const card = toRgba('var(--bg-card)', vars)

  function stacked(component: string, selectors: string[]): Rgba {
    return [...selectors]
      .reverse()
      .reduce(
        (backdrop, selector) =>
          over(toRgba(declaration(ruleBody(component, selector), 'background'), vars), backdrop),
        card,
      )
  }

  it.each(DUPLICATE_SURFACES)(
    '%s carries readable text',
    (_label, componentPath, textSelector, stack) => {
      const component = read(componentPath)
      const text = declaration(ruleBody(component, textSelector), 'color')

      expect(
        contrast(toRgba(text, vars), stacked(component, stack)),
      ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
    },
  )

  it('the count under the filters is readable on the card', () => {
    const text = declaration(ruleBody(read(DUP_QUEUE), '.dup-summary'), 'color')

    expect(contrast(toRgba(text, vars), card)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  it('a refused merge is readable where it lands, off any card', () => {
    const text = declaration(ruleBody(read(DUP_PAGE), '.dup-alert'), 'color')

    expect(
      contrast(toRgba(text, vars), toRgba('var(--bg-primary)', vars)),
    ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})
