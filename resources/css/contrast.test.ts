import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, it, expect } from 'vitest'


const AA_NORMAL_TEXT = 4.5
const BASE = 'resources/css/base.css'
const THEME_ROOT = 'src/web/static/themes'

function themesIn(root: string): [string, string][] {
  const resolved = resolve(process.cwd(), root)
  const themes = readdirSync(resolved, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry): [string, string] => [entry.name, join(root, entry.name, 'colors.css')])
  if (themes.length === 0) throw new Error(`no theme folders under ${resolved}`)
  return themes
}

const THEMES = themesIn(THEME_ROOT)

const TONED_BADGES = [
  ".badge[data-tone='accent']",
  ".badge[data-tone='success']",
  ".badge[data-tone='warning']",
  ".badge[data-tone='error']",
]

const BADGE_TONES = ['.badge', ...TONED_BADGES]

const TONE_CHROMA = 0.1

const BADGE_SURFACES = ['--bg-card', '--bg-elevated', '--bg-primary', '--chrome']

interface Rgba {
  r: number
  g: number
  b: number
  a: number
}

const TRANSPARENT: Rgba = { r: 0, g: 0, b: 0, a: 0 }
const BLACK: Rgba = { r: 0, g: 0, b: 0, a: 1 }

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), 'utf8')
}

function customProperties(source: string): [string, string][] {
  const root = source.match(/:root\s*\{([\s\S]*?)\n\s*\}/)
  return [...(root?.[1] ?? '').matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [
    name,
    value.trim(),
  ])
}

function tokens(themePath: string): Map<string, string> {
  return new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])
}

function ruleBody(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`^${escaped}\\s*(?:,[^{}]*)?\\{([^}]*)\\}`, 'm')
  const match = source.match(pattern)
  if (!match) throw new Error(`${selector} rule not found`)
  return match[1].replace(/\/\*[\s\S]*?\*\//g, '')
}

function optional(body: string, property: string): string | null {
  const match = body.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))
  return match ? match[1].trim().replace(/\s*!important$/, '') : null
}

function declaration(body: string, property: string): string {
  const value = optional(body, property)
  if (value === null) throw new Error(`no ${property} declared`)
  return value
}

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
  if (text === 'black') return BLACK

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

function chroma(colour: Rgba): number {
  const channels = [colour.r, colour.g, colour.b]
  return (Math.max(...channels) - Math.min(...channels)) / 255
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

const IMPORT_SURFACES: [string, string, string, string][] = [
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
  const vars = tokens(themePath)
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

  it.each(DRAGGED_OVER)('%s stays readable with a file over the zone', (textSelector) => {
    const component = read(DROP_ZONE)
    const background = (selector: string): string =>
      declaration(ruleBody(component, selector), 'background')
    const dragged = over(toRgba(background('.drop-zone-over'), vars), card)
    const text = declaration(ruleBody(component, textSelector), 'color')

    expect(contrast(toRgba(text, vars), dragged)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})

describe.each(THEMES)('every badge tone in %s', (_theme, themePath) => {
  const base = read(BASE)
  const vars = tokens(themePath)

  it.each(
    BADGE_TONES.flatMap((tone) =>
      BADGE_SURFACES.map((surface): [string, string] => [tone, surface]),
    ),
  )('%s carries readable text on %s', (selector, surface) => {
    const body = ruleBody(base, selector)
    const beneath = toRgba(`var(${surface})`, vars)
    const tint = optional(body, 'background') ?? optional(ruleBody(base, '.badge'), 'background')
    const text = optional(body, 'color') ?? declaration(ruleBody(base, '.badge'), 'color')

    expect(
      contrast(toRgba(text, vars), over(toRgba(tint!, vars), beneath)),
    ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  const edgeOf = (selector: string): Rgba =>
    toRgba(
      colourIn(
        optional(ruleBody(base, selector), 'border-color') ??
          declaration(ruleBody(base, '.badge'), 'border'),
      ),
      vars,
    )

  it.each(
    BADGE_TONES.flatMap((tone) =>
      BADGE_SURFACES.map((surface): [string, string] => [tone, surface]),
    ),
  )('%s keeps an edge on %s', (selector, surface) => {
    expect(
      contrast(edgeOf(selector), toRgba(`var(${surface})`, vars)),
    ).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(TONED_BADGES)('%s carries its own hue rather than a grey', (selector) => {
    expect(chroma(edgeOf(selector))).toBeGreaterThanOrEqual(TONE_CHROMA)
  })
})

const LIBRARY_FILTERS = 'resources/js/components/organisms/LibraryFilters.vue'
const CONFIRM_PANEL = 'resources/js/components/molecules/ConfirmPanel.vue'
const MODAL_DIALOG = 'resources/js/components/atoms/ModalDialog.vue'

const EDIT_SURFACES: [string, string, string, string, string][] = [
  ['the export scope', LIBRARY_FILTERS, '.export-scope', BASE, '.dropdown-menu'],
  ['an enrichment note', BASE, '.edit-modal-note', MODAL_DIALOG, '.dialog-surface'],
  ['the confirmation question', CONFIRM_PANEL, '.confirm-panel', CONFIRM_PANEL, '.confirm-panel'],
]

describe.each(THEMES)('edit dialog surfaces in %s', (_theme, themePath) => {
  const vars = tokens(themePath)

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

const DUPLICATE_SURFACES: [string, string, string, string[]][] = [
  ['a copy offered twice', DUP_PAIR, '.dup-side-elsewhere', ['.dup-side', '.dup-pair']],
  ['the looser-key caution', DUP_PAIR, '.dup-pair-caution', ['.dup-pair']],
  ['a row title', DUP_PAIR, '.dup-side-title', ['.dup-side', '.dup-pair']],
  ['a row provenance', DUP_PAIR, '.dup-side-meta', ['.dup-side', '.dup-pair']],
  ['a merge details', DUP_HISTORY, '.dup-log-meta', ['.dup-log-row']],
  ['the reason a control refused', DUP_HISTORY, '.dup-log-reason', ['.dup-log-row']],
]

describe.each(THEMES)('duplicates review surfaces in %s', (_theme, themePath) => {
  const vars = tokens(themePath)
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

})

const NON_TEXT = 3

const ACCORDION = 'resources/js/components/atoms/Accordion.vue'
const DATA_PAGE = 'resources/js/components/pages/DataPage.vue'
const NUMBER_STEPPER = 'resources/js/components/atoms/NumberStepper.vue'
const PREFERENCES_PAGE = 'resources/js/components/pages/PreferencesPage.vue'
const SEASON_CHECKLIST = 'resources/js/components/molecules/SeasonChecklist.vue'
const SOURCE_CONFIG_FORM = 'resources/js/components/molecules/SourceConfigForm.vue'
const SOURCE_SYNC_PROGRESS = 'resources/js/components/molecules/SourceSyncProgress.vue'
const STAR_RATING = 'resources/js/components/atoms/StarRating.vue'
const TRAKT_FLOW = 'resources/js/components/molecules/TraktDeviceCodeFlow.vue'
const WEIGHTS = 'resources/js/components/organisms/WeightsDialog.vue'

const MUTED_SURFACES = [
  '--bg-primary',
  '--bg-card',
  '--bg-sidebar',
  '--bg-elevated',
  '--bg-input',
  '--chrome',
]
const EDGE_SURFACES = [...MUTED_SURFACES, '--bg-hover']
const RING_SURFACES = [
  '--bg-card',
  '--bg-input',
  '--bg-primary',
  '--bg-elevated',
  '--bg-sidebar',
  '--chrome',
]

const ACCENT_HOVER_FILLS: [string, string, string][] = [
  [
    'a hovered primary button',
    ".btn-primary:hover:not(:disabled):not([aria-disabled='true'])",
    '.btn-primary',
  ],
  [
    'a hovered chosen type filter',
    "button.badge[aria-checked='true']:hover",
    "button.badge[aria-checked='true']",
  ],
]

describe.each(THEMES)('the token layer in %s', (_theme, themePath) => {
  const vars = tokens(themePath)
  const ratio = (token: string, surface: string): number =>
    contrast(toRgba(`var(${token})`, vars), toRgba(`var(${surface})`, vars))

  it.each(MUTED_SURFACES)('--text-muted stays readable on %s', (surface) => {
    expect(ratio('--text-muted', surface)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  it.each(EDGE_SURFACES)('--border-interactive divides a control from %s', (surface) => {
    expect(ratio('--border-interactive', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(EDGE_SURFACES)('--border-default divides a control from %s', (surface) => {
    expect(ratio('--border-default', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(RING_SURFACES)('--focus-ring rings a focused control on %s', (surface) => {
    expect(ratio('--focus-ring', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it("--focus-ring rings Try again on the error bar's own tint", () => {
    const tint = declaration(ruleBody(read(BASE), '.status-bar.error'), 'background')
    const bar = over(toRgba(tint, vars), toRgba('var(--bg-primary)', vars))

    expect(contrast(toRgba('var(--focus-ring)', vars), bar)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(ACCENT_HOVER_FILLS)(
    '%s keeps its label readable on the --accent-hover it fills with',
    (_label, hoverSelector, labelSelector) => {
      const base = read(BASE)
      const fill = declaration(ruleBody(base, hoverSelector), 'background')
      const text = declaration(ruleBody(base, labelSelector), 'color')

      expect(contrast(toRgba(text, vars), toRgba(fill, vars))).toBeGreaterThanOrEqual(
        AA_NORMAL_TEXT,
      )
    },
  )
})

const CONTROL_EDGES: [string, string, string][] = [
  ['every text entry', BASE, '.field'],
  ['a settings toggle', BASE, '.toggle-switch'],
  ['the file picker button', DROP_ZONE, '.drop-zone-input::file-selector-button'],
]

const LOCKED_FIELDS: [string, string, string, string][] = [
  ['a secret being typed', BASE, '.field', '.field[readonly]'],
]

const BORDER_STYLES = new Set(['solid', 'dashed', 'dotted', 'double'])

function colourIn(value: string): string {
  const parts = value.split(/\s+/)
  const style = parts.findIndex((part) => BORDER_STYLES.has(part))
  return (style === -1 ? parts : parts.slice(style + 1)).join(' ')
}

function borderColour(body: string): string {
  return colourIn(declaration(body, 'border'))
}

function gradientColours(value: string): string[] {
  const gradient = value.match(/^linear-gradient\((.*)\)$/)
  if (!gradient) return [value]
  return commaSeparated(gradient[1])
    .filter((stop) => !/^-?[\d.]+deg$/.test(stop))
    .map((stop) => {
      let depth = 0
      for (let i = 0; i < stop.length; i += 1) {
        if (stop[i] === '(') depth += 1
        else if (stop[i] === ')') depth -= 1
        else if (stop[i] === ' ' && depth === 0) return stop.slice(0, i)
      }
      return stop
    })
}

describe.each(THEMES)('editable control edges in %s', (_theme, themePath) => {
  const vars = tokens(themePath)

  const edgeAgainst = (edge: string, fill: string): number =>
    contrast(toRgba(edge, vars), toRgba(fill, vars))

  it.each(CONTROL_EDGES)('%s is distinguishable from its own fill', (_label, path, selector) => {
    const body = ruleBody(read(path), selector)

    expect(
      edgeAgainst(borderColour(body), declaration(body, 'background')),
    ).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it.each(LOCKED_FIELDS)(
    '%s keeps the value being typed readable',
    (_label, path, editable, locked) => {
      const source = read(path)
      const field = ruleBody(source, editable)
      const lock = ruleBody(source, locked)
      const text = toRgba(declaration(field, 'color'), vars)
      const fade = Number(optional(lock, 'opacity') ?? 1)
      const fill = optional(lock, 'background') ?? declaration(field, 'background')

      expect(
        contrast({ ...text, a: text.a * fade }, toRgba(fill, vars)),
      ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
    },
  )

  it('an unfilled star stays visible against the dialog that rates it', () => {
    const colour = declaration(ruleBody(read(STAR_RATING), '.star-rating-star'), 'color')

    expect(edgeAgainst(colour, 'var(--bg-card)')).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it('the number stepper frames the value rather than blending into it', () => {
    const edge = borderColour(ruleBody(read(BASE), '.field'))
    const fill = declaration(ruleBody(read(NUMBER_STEPPER), '.stepper-input'), 'background')

    expect(edgeAgainst(edge, fill)).toBeGreaterThanOrEqual(NON_TEXT)
  })
})

const TINTED_TEXT: [string, string, string, string][] = [
  ['a page that failed to load', BASE, '.status-bar.error', '--bg-primary'],
  ['a page loading', BASE, '.status-bar.loading', '--bg-primary'],
  ['a preferences save that failed', PREFERENCES_PAGE, '.text-error', '--bg-card'],
  ['a preferences save that landed', PREFERENCES_PAGE, '.text-success', '--bg-card'],
  ['the number of a watched season', SEASON_CHECKLIST, '.season-checkbox.checked', '--bg-card'],
  ['a sync that failed', BASE, '.sync-status-error', '--bg-card'],
  ['a sync still running', BASE, '.sync-status-info', '--bg-card'],
  ['a sync that finished', BASE, '.sync-status-success', '--bg-card'],
  ['a section the rail is not on', BASE, '.nav-item', '--chrome'],
  ['the section the rail marks', BASE, ".nav-item[aria-current]", '--chrome'],
  ['the account the rail names', BASE, '.nav-user', '--chrome'],
  ['the app name on the top strip', BASE, '.app-name', '--chrome'],
  ['the weights panel saying what it is for', WEIGHTS, '.weights-lede', '--bg-card'],
  ['a weights save that failed', WEIGHTS, '.weights-status.failed', '--bg-card'],
  ['a scorer weight', BASE, '.slider-value', '--bg-card'],
  ['a page, panel or field that refused', BASE, '.state--error', '--bg-card'],
  ['the same refusal off any card', BASE, '.state--error', '--bg-primary'],
  ['a new version being available', BASE, '.update-banner', '--bg-primary'],
  ['the Reload button on it', BASE, '.btn-secondary', '--bg-primary'],
  ['the type glyph standing in for a missing cover', BASE, '.cover-art--none', '--bg-card'],
  ['a recommendation set aside', BASE, '.rec-aside-body', '--bg-primary'],
  ['the heading over a breakdown', BASE, '.score-details-title', '--bg-primary'],
  ['a scorer in a breakdown', BASE, '.score-label', '--bg-primary'],
  ['how far that scorer reached', BASE, '.score-value', '--bg-primary'],
  ['the points variety took off', BASE, '.score-row-penalty .score-value', '--bg-primary'],
  ['a hovered scorer tooltip', BASE, '.scorer-tooltip-wrap:hover .scorer-tooltip-icon', '--bg-card'],
  ['the Trakt activation link', TRAKT_FLOW, '.trakt-flow-link', '--bg-card'],
  ['a Trakt connect failure', TRAKT_FLOW, '.trakt-flow-status--error', '--bg-card'],
]

const SCORE_BUTTON_TEXT = ['.rec-score-caption', '.rec-score-cue']

describe.each(THEMES)('text over the surface it lands on in %s', (_theme, themePath) => {
  const vars = tokens(themePath)

  it.each(TINTED_TEXT)('%s says so readably', (_label, path, selector, surface) => {
    const body = ruleBody(read(path), selector)
    const beneath = toRgba(`var(${surface})`, vars)
    const tint = optional(body, 'background')
    const behind = tint ? over(toRgba(tint, vars), beneath) : beneath
    const text = toRgba(declaration(body, 'color'), vars)
    const fade = Number(optional(body, 'opacity') ?? 1)

    expect(contrast({ ...text, a: text.a * fade }, behind)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  it.each(SCORE_BUTTON_TEXT)('%s stays readable on the fill the score takes', (selector) => {
    const base = read(BASE)
    const fill = declaration(ruleBody(base, '.rec-score:hover'), 'background')
    const text = declaration(ruleBody(base, selector), 'color')

    expect(contrast(toRgba(text, vars), toRgba(fill, vars))).toBeGreaterThanOrEqual(
      AA_NORMAL_TEXT,
    )
  })
})

const RESTORED_SURFACES: [string, string, string, string, string][] = [
  ['an accordion header', ACCORDION, '.accordion-trigger', ACCORDION, '.accordion'],
  ['the All Sources hint', BASE, '.sync-plugin-name', DATA_PAGE, '.sync-all-card'],
]

describe.each(THEMES)('surfaces restored to a defined token in %s', (_theme, themePath) => {
  const vars = tokens(themePath)

  it.each(RESTORED_SURFACES)(
    '%s reads against the background that now paints',
    (_label, textPath, textSelector, surfacePath, surfaceSelector) => {
      const text = declaration(ruleBody(read(textPath), textSelector), 'color')
      const surface = declaration(ruleBody(read(surfacePath), surfaceSelector), 'background')

      expect(contrast(toRgba(text, vars), toRgba(surface, vars))).toBeGreaterThanOrEqual(
        AA_NORMAL_TEXT,
      )
    },
  )
})

const BUTTON_LABELS: [string, string, string, string][] = [
  ['Delete', BASE, '.btn-danger', '.btn-danger'],
  ['a primary action', BASE, '.btn-primary', '.btn-primary'],
  ['Enable', SOURCE_CONFIG_FORM, ':deep(.btn-success)', ':deep(.btn-success)'],
]

describe.each(THEMES)('button labels on the fills they carry in %s', (_theme, themePath) => {
  const vars = tokens(themePath)
  const paint = (path: string, selector: string, property = 'background'): Rgba =>
    toRgba(declaration(ruleBody(read(path), selector), property), vars)

  it.each(BUTTON_LABELS)('%s stays readable', (_label, path, labelSelector, fillSelector) => {
    expect(
      contrast(paint(path, labelSelector, 'color'), paint(path, fillSelector)),
    ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  it('a locked button carries a fill no live variant does, under a readable label', () => {
    const lock = "button.btn[aria-disabled='true']"
    const fill = paint(BASE, lock)
    const live = ['.btn-primary', '.btn-secondary', '.btn-ghost', '.btn-danger']
      .map((v) => paint(BASE, v))
      .concat(paint(SOURCE_CONFIG_FORM, ':deep(.btn-success)'))

    expect(live).not.toContainEqual(fill)
    expect(contrast(paint(BASE, lock, 'color'), fill)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})

const BARS: [string, string, string, string][] = [
  ['a scorer contribution', BASE, '.score-bar-bg', '.score-bar-fill'],
  ['a variety penalty', BASE, '.score-bar-bg', '.score-bar-fill-penalty'],
  ['a source sync', SOURCE_SYNC_PROGRESS, '.source-progress-bar', '.source-progress-fill'],
  ['a scorer weight', BASE, 'input[type="range"]', 'input[type="range"]'],
]

describe.each(THEMES)('how far a bar has filled in %s', (_theme, themePath) => {
  const vars = tokens(themePath)
  const card = toRgba('var(--bg-card)', vars)

  const boundedBy = (fills: string[], track: Rgba, edge: string | null): void => {
    expect(fills, 'flat bar measures nothing').not.toEqual([])
    for (const fill of fills) {
      expect(contrast(toRgba(fill, vars), track)).toBeGreaterThanOrEqual(NON_TEXT)
      expect(contrast(toRgba(fill, vars), card)).toBeGreaterThanOrEqual(NON_TEXT)
    }
    const outline = edge === null ? track : toRgba(colourIn(edge), vars)
    expect(contrast(outline, card), 'track lost on the card').toBeGreaterThanOrEqual(NON_TEXT)
  }

  it.each(BARS)('%s is bounded by its track', (_label, path, trackSelector, fillSelector) => {
    const source = read(path)
    const body = ruleBody(source, trackSelector)
    const stops = gradientColours(declaration(body, 'background'))
    const track = stops[stops.length - 1]
    const fills = gradientColours(declaration(ruleBody(source, fillSelector), 'background'))

    boundedBy(
      fills.filter((colour) => colour !== track),
      toRgba(track, vars),
      optional(body, 'border'),
    )
  })
})

const CONTROL_BOUNDARIES: [string, string, string, string, string][] = [
  ['a refused field', BASE, ".field[aria-invalid='true']", 'border-color', 'var(--bg-input)'],
  ['a season checkbox', SEASON_CHECKLIST, '.season-checkbox', 'border', 'var(--bg-elevated)'],
  ['a leading score segment', BASE, '.rec-spine-segment', 'background', 'var(--bg-card)'],
  [
    'the rest of the score',
    BASE,
    '.rec-spine-segment--rest',
    'background',
    'var(--bg-card)',
  ],
  [
    'the variety penalty in it',
    BASE,
    '.rec-spine-segment--penalty',
    'background',
    'var(--bg-card)',
  ],
  ['the box a cover sits in', BASE, '.cover-art', 'border', 'var(--bg-card)'],
  ['a scorer tooltip', BASE, '.scorer-tooltip-text', 'border', 'var(--bg-card)'],
  ['a scorer tooltip tail', BASE, '.scorer-tooltip-text::after', 'border-top-color', 'var(--bg-card)'],
  [
    'a weights tooltip tail',
    WEIGHTS,
    '.weights-body :deep(.scorer-tooltip-text::after)',
    'border-bottom-color',
    'var(--bg-card)',
  ],
]

function invalidEdges(source: string): string[] {
  return [...source.matchAll(/\[aria-invalid=['"]true['"]\][^{}]*\{([^}]*)\}/g)].flatMap(
    ([, body]) => optional(body, 'border-color') ?? optional(body, 'border') ?? [],
  )
}

function styledFiles(directory: string): string[] {
  return readdirSync(resolve(process.cwd(), directory), { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`
    return entry.isDirectory() ? styledFiles(path) : /\.(css|vue)$/.test(path) ? [path] : []
  })
}

describe.each(THEMES)('edges that say where a control is in %s', (_theme, themePath) => {
  const vars = tokens(themePath)

  const divides = (edge: string, surface: Rgba): number => contrast(toRgba(edge, vars), surface)

  it.each(CONTROL_BOUNDARIES)(
    '%s is visible against what it encloses',
    (_label, path, selector, property, surface) => {
      const edge = colourIn(declaration(ruleBody(read(path), selector), property))

      expect(divides(edge, toRgba(surface, vars))).toBeGreaterThanOrEqual(NON_TEXT)
    },
  )

  it('the spinner segment that turns is told apart from the ring around it', () => {
    const spinner = ruleBody(read(BASE), '.spinner')
    const page = toRgba('var(--bg-primary)', vars)
    const segment = over(toRgba(declaration(spinner, 'border-top-color'), vars), page)
    const ring = toRgba(colourIn(declaration(spinner, 'border')), vars)

    expect(contrast(segment, ring)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it('a secondary button keeps an edge on the bar reporting a failure', () => {
    const base = read(BASE)
    const tint = declaration(ruleBody(base, '.status-bar.error'), 'background')
    const banner = over(toRgba(tint, vars), toRgba('var(--bg-primary)', vars))
    const edge = colourIn(declaration(ruleBody(base, '.btn-secondary'), 'border-color'))

    expect(divides(edge, banner)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  it('every rule marking a refused field draws an edge on the fill a field carries', () => {
    const field = toRgba('var(--bg-input)', vars)
    const edges = styledFiles('resources').flatMap((path) => invalidEdges(read(path)))

    expect(edges.length).toBeGreaterThan(0)
    for (const edge of edges) {
      expect(divides(colourIn(edge), field), edge).toBeGreaterThanOrEqual(NON_TEXT)
    }
  })

  it('a refusal is marked by a rule visible on its own tint', () => {
    const body = ruleBody(read(BASE), '.state--error')
    const tint = over(toRgba(declaration(body, 'background'), vars), toRgba('var(--bg-card)', vars))

    expect(divides(colourIn(declaration(body, 'border-left')), tint)).toBeGreaterThanOrEqual(
      NON_TEXT,
    )
  })
})

describe('the refused-field sweep', () => {
  it('reads the double-quoted attribute a formatter emits, not the single one alone', () => {
    expect(invalidEdges('[aria-invalid="true"] {\n  border-color: red;\n}')).toEqual(['red'])
  })
})

describe('the theme scan', () => {
  it('measures every folder it finds, and names the colors.css one of them lacks', () => {
    const root = mkdtempSync(join(tmpdir(), 'contrast-'))
    const palette = (theme: string): string => join(root, theme, 'colors.css')
    mkdirSync(join(root, 'unreadable'))
    writeFileSync(palette('unreadable'), ':root {\n  --text-muted: #3b4252;\n}\n')
    mkdirSync(join(root, 'paletteless'))

    try {
      expect(themesIn(root).map(([, path]) => path)).toEqual(
        expect.arrayContaining([palette('unreadable'), palette('paletteless')]),
      )
      expect(() => tokens(palette('paletteless'))).toThrow(palette('paletteless'))
      const vars = tokens(palette('unreadable'))
      expect(
        contrast(toRgba('var(--text-muted)', vars), toRgba('var(--bg-card)', vars)),
      ).toBeLessThan(AA_NORMAL_TEXT)
    } finally {
      rmSync(root, { recursive: true })
    }
  })

  it('names an empty root and throws, rather than registering zero tests', () => {
    const root = mkdtempSync(join(tmpdir(), 'contrast-'))

    try {
      expect(() => themesIn(root)).toThrow(root)
    } finally {
      rmSync(root, { recursive: true })
    }
  })
})
