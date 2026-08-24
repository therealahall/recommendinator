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
    '3.21': ['var(--accent-light)', 'color-mix(in srgb, var(--accent) 15%, transparent)'],
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

/** Finds the rule the selector opens, whether or not it shares it with others.
 *  Anchored so `.pill` never resolves to `.pill-group`. */
function ruleBody(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`^${escaped}\\s*(?:,[^{}]*)?\\{([^}]*)\\}`, 'm')
  const match = source.match(pattern)
  if (!match) throw new Error(`${selector} rule not found`)
  return match[1]
}

function optional(body: string, property: string): string | null {
  const match = body.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))
  return match ? match[1].trim() : null
}

function declaration(body: string, property: string): string {
  const value = optional(body, property)
  if (value === null) throw new Error(`no ${property} declared`)
  return value
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
const CONFIRM_PANEL = 'resources/js/components/molecules/ConfirmPanel.vue'
const MODAL_DIALOG = 'resources/js/components/atoms/ModalDialog.vue'

/** Text and the rule painting what it sits on, which for two of the three is
 *  declared in another file. */
const EDIT_SURFACES: [string, string, string, string, string][] = [
  ['the export scope', LIBRARY_FILTERS, '.export-scope', BASE, '.dropdown-menu'],
  ['an enrichment note', BASE, '.edit-modal-note', MODAL_DIALOG, '.dialog-surface'],
  ['the confirmation question', CONFIRM_PANEL, '.confirm-panel', CONFIRM_PANEL, '.confirm-panel'],
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

const NON_TEXT = 3

const ACCORDION = 'resources/js/components/atoms/Accordion.vue'
const ADD_SOURCE_MODAL = 'resources/js/components/organisms/AddSourceModal.vue'
const AUTH_FIELD = 'resources/js/components/atoms/AuthField.vue'
const DATA_PAGE = 'resources/js/components/pages/DataPage.vue'
const NUMBER_STEPPER = 'resources/js/components/atoms/NumberStepper.vue'
const PREFERENCES_PAGE = 'resources/js/components/pages/PreferencesPage.vue'
const RECOMMENDATIONS_PAGE = 'resources/js/components/pages/RecommendationsPage.vue'
const SEARCH_INPUT = 'resources/js/components/atoms/SearchInput.vue'
const SEASON_CHECKLIST = 'resources/js/components/molecules/SeasonChecklist.vue'
const SETTING_CONTROL = 'resources/js/components/molecules/SettingControl.vue'
const SETTING_SECRET = 'resources/js/components/molecules/SettingSecret.vue'
const SOURCE_CONFIG_FORM = 'resources/js/components/molecules/SourceConfigForm.vue'
const STAR_RATING = 'resources/js/components/atoms/StarRating.vue'
const TRAKT_FLOW = 'resources/js/components/molecules/TraktDeviceCodeFlow.vue'

const MUTED_SURFACES = ['--bg-primary', '--bg-card', '--bg-sidebar', '--bg-elevated', '--bg-input']
const CONTROL_SURFACES = ['--bg-card', '--bg-input', '--bg-elevated']
const RING_SURFACES = [
  '--bg-card',
  '--bg-input',
  '--bg-primary',
  // The CSV drop zone and the export menu, both of which a Tab reaches.
  '--bg-elevated',
  '--bg-sidebar',
]

describe.each(THEMES)('the token layer in %s', (_theme, themePath) => {
  const vars = new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])
  const ratio = (token: string, surface: string): number =>
    contrast(toRgba(`var(${token})`, vars), toRgba(`var(${surface})`, vars))

  // Help text, empty states, hints and secondary labels all default to this at
  // 12-13px, so a theme cannot tune it as a decorative grey.
  it.each(MUTED_SURFACES)('--text-muted stays readable on %s', (surface) => {
    expect(ratio('--text-muted', surface)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })

  // In a light theme a field and the card behind it are both white, so this
  // edge is the only thing saying where the field is (WCAG 1.4.11).
  it.each(CONTROL_SURFACES)('--border-interactive divides a control from %s', (surface) => {
    expect(ratio('--border-interactive', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })

  // The app's one focus indicator, and the measurement THEME_DEVELOPMENT.md
  // promises a theme author.
  it.each(RING_SURFACES)('--accent-light rings a focused control on %s', (surface) => {
    expect(ratio('--accent-light', surface)).toBeGreaterThanOrEqual(NON_TEXT)
  })
})

/** Controls that declare both their edge and the fill it encloses. */
const CONTROL_EDGES: [string, string, string][] = [
  ['a preferences field', BASE, '.form-group select'],
  ['a library filter select', BASE, '.toolbar-select'],
  ['a content-type pill', BASE, '.pill'],
  ['a settings toggle', BASE, '.toggle-switch'],
  ['the theme select', BASE, '.length-select'],
  ['the add-rule field', BASE, '.add-rule-form input[type="text"]'],
  ['an edit-modal field', BASE, '.edit-field input'],
  ['an OAuth code field', BASE, '.gog-input-row input'],
  ['the library search field', SEARCH_INPUT, '.search-input'],
  ['a source-config field', SOURCE_CONFIG_FORM, '.source-form-field input[type="text"]'],
  ['a source-config chips field', SOURCE_CONFIG_FORM, '.chips-field'],
  ['an add-source field', ADD_SOURCE_MODAL, '.add-source-field input[type="text"]'],
  ['a settings field', SETTING_CONTROL, ".setting-control input[type='text']"],
  ['a settings secret field', SETTING_SECRET, ".secret-edit-row input[type='password']"],
  ['a sign-in field', AUTH_FIELD, '.auth-field input'],
  ['the file picker button', DROP_ZONE, '.drop-zone-input::file-selector-button'],
]

/** Secret fields that go `readonly` mid-write: the rule painting one open, then
 *  the rule painting it locked. */
const LOCKED_FIELDS: [string, string, string, string][] = [
  [
    'a source secret',
    SOURCE_CONFIG_FORM,
    '.source-form-field input[type="text"]',
    '.source-form-field input[readonly]',
  ],
  [
    'a settings secret',
    SETTING_SECRET,
    ".secret-edit-row input[type='password']",
    '.secret-edit-row input[readonly]',
  ],
]

/** The colour out of a `border: <width> <style> <colour>` shorthand. */
function borderColour(body: string): string {
  return declaration(body, 'border').split(/\s+/).slice(2).join(' ')
}

describe.each(THEMES)('editable control edges in %s', (_theme, themePath) => {
  const vars = new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])

  const edgeAgainst = (edge: string, fill: string): number =>
    contrast(toRgba(edge, vars), toRgba(fill, vars))

  it.each(CONTROL_EDGES)('%s is distinguishable from its own fill', (_label, path, selector) => {
    const body = ruleBody(read(path), selector)

    expect(
      edgeAgainst(borderColour(body), declaration(body, 'background')),
    ).toBeGreaterThanOrEqual(NON_TEXT)
  })

  // `readonly` is not exempt from 1.4.3 the way `disabled` is. Folding opacity
  // is what CONTROL_EDGES never did, which is how a 3.69:1 fade shipped.
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
    const source = read(NUMBER_STEPPER)
    const edge = borderColour(ruleBody(source, '.number-stepper'))

    expect(
      edgeAgainst(edge, declaration(ruleBody(source, '.stepper-input'), 'background')),
    ).toBeGreaterThanOrEqual(NON_TEXT)
  })
})

/** Text that paints its own tint, or none at all, over the surface it lands on. */
const TINTED_TEXT: [string, string, string, string][] = [
  ['a page that failed to load', BASE, '.status-bar.error', '--bg-primary'],
  ['a page loading', BASE, '.status-bar.loading', '--bg-primary'],
  ['a preferences save that failed', PREFERENCES_PAGE, '.text-error', '--bg-card'],
  ['a preferences save that landed', PREFERENCES_PAGE, '.text-success', '--bg-card'],
  ['the number of a watched season', SEASON_CHECKLIST, '.season-checkbox.checked', '--bg-card'],
  ['the version under the app name', BASE, '.version-label', '--bg-sidebar'],
  ['a sync that failed', BASE, '.sync-status-error', '--bg-card'],
  ['a sync still running', BASE, '.sync-status-info', '--bg-card'],
  ['a sync that finished', BASE, '.sync-status-success', '--bg-card'],
  ['the page the sidebar is on', BASE, '.nav-item.active', '--bg-sidebar'],
  ['the app name over the nav', BASE, '.sidebar-header h1', '--bg-sidebar'],
  ['a scorer weight', BASE, '.slider-value', '--bg-card'],
  ['a content-type badge', BASE, '.badge-type', '--bg-card'],
  ['a score badge', BASE, '.badge-score', '--bg-card'],
  ['a hovered score breakdown', BASE, '.score-details summary:hover', '--bg-card'],
  ['the Trakt activation link', TRAKT_FLOW, '.trakt-flow-link', '--bg-card'],
]

describe.each(THEMES)('text over the surface it lands on in %s', (_theme, themePath) => {
  const vars = new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])

  // opacity is folded into the measurement rather than forbidden, because a
  // faded token reads as its blend and a rule is free to earn the ratio anyway.
  it.each(TINTED_TEXT)('%s says so readably', (_label, path, selector, surface) => {
    const body = ruleBody(read(path), selector)
    const beneath = toRgba(`var(${surface})`, vars)
    const tint = optional(body, 'background')
    const behind = tint ? over(toRgba(tint, vars), beneath) : beneath
    const text = toRgba(declaration(body, 'color'), vars)
    const fade = Number(optional(body, 'opacity') ?? 1)

    expect(contrast({ ...text, a: text.a * fade }, behind)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT)
  })
})

/** Backgrounds that named an undefined token, so they painted nothing and the
 *  text inside them was measured against a surface it never met. */
const RESTORED_SURFACES: [string, string, string, string, string][] = [
  ['an accordion header', ACCORDION, '.accordion-trigger', ACCORDION, '.accordion'],
  ['the All Sources hint', BASE, '.sync-plugin-name', DATA_PAGE, '.sync-all-card'],
  [
    'an ignored recommendation',
    RECOMMENDATIONS_PAGE,
    '.rec-ignored',
    RECOMMENDATIONS_PAGE,
    '.rec-ignored',
  ],
]

describe.each(THEMES)('surfaces restored to a defined token in %s', (_theme, themePath) => {
  const vars = new Map([...customProperties(read(BASE)), ...customProperties(read(themePath))])

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
