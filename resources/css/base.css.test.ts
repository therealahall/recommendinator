import { readFileSync, readdirSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// base.css is a static asset: importing it through Vite yields an empty stub
// under Vitest, so read the file off disk to assert on its real contents.

// Isolate the `.sr-only { ... }` declaration block so the assertion cannot be
// satisfied by an unrelated rule that happens to mention user-select.
function srOnlyBlock(source: string): string {
  const match = source.match(/\.sr-only\s*\{([^}]*)\}/)
  if (!match) throw new Error('.sr-only rule not found in base.css')
  return match[1]
}

function readBase(): string {
  return readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
}

// Requiring `{` immediately after the selector keeps a lookup off a longer
// selector that starts with the same text, or a descendant rule under it.
function ruleBlock(source: string, selector: string): string {
  const escaped = selector.replace(/\./g, '\\.')
  const match = source.match(new RegExp(`(?:^|[\\s}])${escaped}\\s*\\{([^}]*)\\}`))
  if (!match) throw new Error(`${selector} rule not found`)
  return match[1]
}

function declaration(block: string, property: string): string {
  // Strip comments first: prose in this file names the properties it explains.
  const match = block
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .match(new RegExp(`(?<![-\\w])${property}:\\s*([^;]+);`))
  if (!match) throw new Error(`${property} not declared`)
  return match[1].trim()
}

function mediaBlock(source: string, maxWidth: string): string {
  const match = source.match(new RegExp(`@media \\(max-width: ${maxWidth}\\) \\{([\\s\\S]*?)\\n\\}`))
  if (!match) throw new Error(`${maxWidth} media block not found in base.css`)
  return match[1]
}

describe('the type scale', () => {
  it('sizes every rule off it, so a text-only zoom moves all the text', () => {
    // An absolute font-size ignores the text size the browser was set to
    // (WCAG 1.4.4), and the scale itself is declared in rem.
    const scanned = styledFiles('resources')
    const fixed = scanned.flatMap((path) =>
      [
        ...readFileSync(`${process.cwd()}/${path}`, 'utf8')
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .matchAll(/(?<![-\w])font-size:\s*([^;]+);/g),
      ].flatMap(([, value]) => (/\d(px|pt)\b/.test(value) ? [`${path}: ${value.trim()}`] : [])),
    )

    expect(scanned.length).toBeGreaterThan(0)
    expect(fixed).toEqual([])
  })
})

const WCAG_FLOORS = new Set(['3:1', '4.5:1', '7:1'])

describe('ratios written into the stylesheets', () => {
  it('names a WCAG floor, never a measurement that goes stale when a theme moves', () => {
    const scanned = styledFiles('resources', /\.(css|vue|ts)$/)
    const measured = scanned.flatMap((path) =>
      [
        ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(/\d+(?:\.\d+)?:1(?![\d.])/g),
      ].flatMap(([ratio]) => (WCAG_FLOORS.has(ratio) ? [] : [`${path}: ${ratio}`])),
    )

    expect(scanned.length).toBeGreaterThan(0)
    expect(measured).toEqual([])
  })
})

describe('the scorer tooltip', () => {
  it('opens inside a 320px viewport rather than off the side of it', () => {
    // 260px centred on its trigger hung half the box past the left edge of the
    // page, and nothing about it could shrink (WCAG 1.4.10).
    expect(declaration(ruleBlock(readBase(), '.scorer-tooltip-text'), 'transform')).toBe('none')
    expect(declaration(ruleBlock(readBase(), '.scorer-tooltip-text'), 'max-width')).toContain('100vw')
  })

  it('opens down and back inside the weights panel, which scrolls its rows', () => {
    // Above the trigger or right of it, the box lands in overflow that scroll
    // box cannot reach, and the row at the top of it loses its help entirely.
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/organisms/WeightsDialog.vue`,
      'utf8',
    )
    const match = source.match(/\.weights-body :deep\(\.scorer-tooltip-text\)\s*\{([^}]*)\}/)
    if (!match) throw new Error('weights-body tooltip rule not found')

    expect(declaration(match[1], 'top')).toContain('100%')
    expect(declaration(match[1], 'bottom')).toBe('auto')
    expect(declaration(match[1], 'left')).toBe('auto')
    expect(declaration(match[1], 'max-width')).toContain('100vw')
  })
})

describe('inactive button styling', () => {
  // Regression: the project had NO `.btn:disabled` rule at all. The fade is
  // the pre-interaction lock's alone; contrast.test.ts measures why.
  it('re-cursors both inactive spellings and dims the exempt one', () => {
    const source = readBase()
    const locked = source.match(/\.btn:disabled,\s*\.toggle-switch:disabled\s*\{([^}]*)\}/)
    const inFlight = source.match(/\.btn\[aria-disabled='true'\]\s*\{([^}]*)\}/)
    if (!locked || !inFlight) throw new Error('inactive-button rules not found in base.css')

    expect(locked[1]).toMatch(/opacity:/)
    expect(locked[1]).toMatch(/cursor:\s*not-allowed/)
    expect(inFlight[1]).toMatch(/cursor:\s*not-allowed/)
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

describe('the one text-entry field', () => {
  // Both are (0,2,0), so order alone decides. Declared the other way round the
  // focus ring repainted the edge and the refusal vanished under the cursor —
  // which is what the `!important` this replaced existed to fight.
  it('keeps a refused edge while the field is focused', () => {
    const source = readBase()

    expect(source.indexOf(".field[aria-invalid='true'] {")).toBeGreaterThan(
      source.indexOf('.field:focus {'),
    )
  })

  // aria-invalid is what the rule keys off, so the assistive-tech signal and
  // the visible one cannot drift apart.
  it('draws its refusal off the same attribute assistive tech reads', () => {
    const match = readBase().match(/\.field\[aria-invalid='true'\]\s*\{([^}]*)\}/)
    if (!match) throw new Error('no refused-field rule in base.css')

    expect(declaration(match[1], 'border-color')).toBe('var(--color-error-text)')
  })
})

describe('a status region with nothing to say', () => {
  // It stays in the accessibility tree so a later message is announced rather
  // than read as inserted content (WCAG 4.1.3) — which means it must not paint
  // an empty tinted block on every screen that mounts one.
  it('takes no space while it is silent', () => {
    const empty = ruleBlock(readBase(), '.state--error:empty')

    expect(declaration(empty, 'padding')).toBe('0')
    expect(declaration(empty, 'border-left-width')).toBe('0')
    expect(declaration(empty, 'background')).toBe('none')
  })
})

describe('error text token', () => {
  it('derives error text from the active palette, not the fill colour', () => {
    // --color-error is sized for fills and falls short of 4.5:1 as text.
    const source = readBase()
    const match = source.match(/--color-error-text:([^;]*);/)
    if (!match) throw new Error('--color-error-text declaration not found in base.css')

    expect(match[1]).toContain('var(--color-error)')
    expect(match[1]).toContain('var(--text-primary)')
  })
})

describe('the reset Tailwind used to supply', () => {
  // These read as dead resets, so a tidy-up deletes them — and each is the
  // only rule covering its surface.
  it('leaves an unsized heading at body size and weight', () => {
    const match = readBase().match(/h1,\s*h2,\s*h3,\s*h4,\s*h5,\s*h6\s*\{([^}]*)\}/)
    if (!match) throw new Error('heading reset not found in base.css')

    expect(match[1]).toMatch(/font-size:\s*inherit/)
    expect(match[1]).toMatch(/font-weight:\s*inherit/)
  })

  it('gives every form control the page font rather than the UA one', () => {
    const match = readBase().match(
      /button,\s*input,\s*optgroup,\s*select,\s*textarea\s*\{([^}]*)\}/,
    )
    if (!match) throw new Error('form-control font reset not found in base.css')

    expect(match[1]).toMatch(/font:\s*inherit/)
  })

  it('lays an icon out as a block instead of on a text baseline', () => {
    const match = readBase().match(/^svg\s*\{([^}]*)\}/m)
    if (!match) throw new Error('svg display rule not found in base.css')

    expect(match[1]).toMatch(/display:\s*block/)
  })
})

describe('the stale-bundle banner', () => {
  // The banner is one flex item, so its min-content floor is its longest word
  // — and that word is --renew-anon-volumes unless the token can break.
  it('lets the recovery command break rather than set the row floor', () => {
    expect(declaration(ruleBlock(readBase(), '.update-banner code'), 'overflow-wrap')).toBe(
      'anywhere',
    )
  })
})

describe('the item a sync is working on', () => {
  it('runs to the whole title rather than clipping it, and never widens the page', () => {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/molecules/SourceSyncProgress.vue`,
      'utf8',
    )
    const rule = ruleBlock(source, '.source-progress-item')

    expect(rule).not.toMatch(/text-overflow:\s*ellipsis/)
    expect(declaration(rule, 'overflow-wrap')).toBe('anywhere')
  })
})

describe('library card divider (issue #108)', () => {
  // margin-top:auto is zero on a content-height card, so on a one-column grid
  // the divider touched the badges — but looked right whenever the card was
  // rated.
  it('spaces the badges off the divider whether or not the card is rated', () => {
    const source = readBase()
    const gap = declaration(ruleBlock(source, '.library-meta'), 'margin-bottom')

    expect(gap).not.toBe('0')
    expect(declaration(ruleBlock(source, '.library-meta-secondary'), 'margin-bottom')).toBe(gap)
  })
})

describe('the one cover box', () => {
  // 16:9 key art and square art arrive in the same 2:3 box as a poster, so
  // anything but `cover` renders them stretched or letterboxed.
  it('slices art of any source ratio rather than distorting it', () => {
    const source = readBase()

    expect(declaration(ruleBlock(source, '.cover-art img'), 'object-fit')).toBe('cover')
    expect(declaration(ruleBlock(source, '.cover-art img'), 'object-position')).toBe('center')
  })

  // Slicing to a box off the poster ratio crops the art every source ships,
  // rather than only the wider key art it is there for.
  it('holds the 2:3 the covers themselves are, at every size it is set to', () => {
    const source = readBase()
    const box = ruleBlock(source, '.cover-art')
    const ratio = parseFloat(
      /calc\(\s*var\(--cover-height\)\s*\/\s*([\d.]+)\s*\)/.exec(declaration(box, 'width'))?.[1] ??
        'NaN',
    )

    expect(declaration(box, 'height')).toBe('var(--cover-height)')
    expect(ratio).toBeCloseTo(1.5)
  })

  // A <span> is inline, where width and height do not apply, so the box was only
  // ever sized by a flex parent blockifying it.
  it('carries its own box rather than the layout its parent happens to use', () => {
    const box = ruleBlock(readBase(), '.cover-art')

    expect(declaration(box, 'display')).not.toBe('inline')
    expect(declaration(box, 'max-width')).toBe('100%')
  })
})

describe('recommendation card header (issue #98)', () => {
  // 1.4.10: at 375px the score and the rank column squeezed the title until the
  // card scrolled sideways, so nothing sharing the identity line holds a width.
  it('drops the score below the title before it can crowd it', () => {
    const base = readBase()
    const mobile = mediaBlock(base, '640px')

    expect(declaration(ruleBlock(base, '.rec-card'), 'grid-template-columns')).toContain(
      'minmax(0, 1fr)',
    )
    expect(declaration(ruleBlock(mobile, '.rec-header'), 'flex-direction')).toBe('column')
    expect(declaration(ruleBlock(mobile, '.rec-header'), 'min-width')).toBe('0')
    expect(declaration(ruleBlock(base, '.rec-identity'), 'min-width')).toBe('0')
  })

  it('breaks a long title rather than widening the card holding it', () => {
    expect(declaration(ruleBlock(readBase(), '.rec-title'), 'overflow-wrap')).toBe('anywhere')
  })
})

describe('one-handed reach on a phone', () => {
  it('gives the sign-in submit a thumb-sized target', () => {
    const match = readBase().match(/\.auth-submit\s*\{([^}]*)\}/)
    if (!match) throw new Error('.auth-submit rule not found in base.css')

    expect(match[1]).toMatch(/min-height:\s*44px/)
  })

  it('gives a badge a thumb-sized target once it is a button (WCAG 2.5.8)', () => {
    // 20px tall in a group gapped 4px reaches no spacing exception either.
    const source = readBase()

    expect(declaration(ruleBlock(source, 'button.badge'), 'min-height')).toBe('44px')
    expect(ruleBlock(source, '.badge')).not.toMatch(/min-height:/)
  })

  it('gives every control standing in a filter row the same thumb target', () => {
    // The pills were 44px and the stepper, the selects and the buttons beside
    // them 35, so one row disagreed with itself about what a target is.
    const row = readBase().match(/\.toolbar \.btn,\s*\.toolbar \.field\s*\{([^}]*)\}/)
    if (!row) throw new Error('no shared toolbar control height in base.css')
    const stepper = readFileSync(
      `${process.cwd()}/resources/js/components/atoms/NumberStepper.vue`,
      'utf8',
    )

    expect(declaration(row[1], 'min-height')).toBe('44px')
    expect(declaration(ruleBlock(stepper, '.number-stepper'), 'min-height')).toBe('44px')
  })

  it('keeps the profile panel action a thumb target, small variant or not', () => {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/organisms/ProfilePanel.vue`,
      'utf8',
    )

    expect(declaration(ruleBlock(source, '.pref-section > .btn'), 'min-height')).toBe('44px')
  })

  it('gives the preferences commit buttons a thumb target where the page has no pointer', () => {
    const mobile = mediaBlock(
      readFileSync(`${process.cwd()}/resources/js/components/pages/PreferencesPage.vue`, 'utf8'),
      '768px',
    )

    expect(declaration(ruleBlock(mobile, '.pref-actions .btn'), 'min-height')).toBe('44px')
  })

  it('keeps a drag past the top off the browser reload, from the root the viewport reads', () => {
    const contained = [...readBase().matchAll(/([^{}]*)\{([^{}]*)\}/g)].flatMap(
      ([, selector, body]) =>
        /overscroll-behavior-y:\s*(?:contain|none)/.test(body) ? [selector.trim()] : [],
    )

    expect(contained.length).toBeGreaterThan(0)
    expect(contained.some((selector) => /(?:^|,)\s*html\s*(?:,|$)/m.test(selector))).toBe(true)
  })

  it('sizes the standalone screens to the visible viewport, not the tallest one', () => {
    // 100vh alone strands the submit button under a collapsing mobile toolbar.
    const match = readBase().match(/\.auth-screen\s*\{([^}]*)\}/)
    if (!match) throw new Error('.auth-screen rule not found in base.css')

    expect(match[1]).toMatch(/min-height:\s*100dvh/)
  })
})

function coarsePointerBlock(source: string): string {
  const match = source.match(/@media \(hover: none\) and \(pointer: coarse\) \{([\s\S]*?)\n\}/)
  if (!match) throw new Error('coarse-pointer media block not found in base.css')
  return match[1]
}

describe('typing into the app on a phone', () => {
  it('never sizes a control below the 16px mobile Safari zooms the page at', () => {
    const touch = coarsePointerBlock(readBase())

    expect(declaration(ruleBlock(touch, ':root'), '--control-text')).toBe('1rem')
    // The element rule, for a control carrying no class of its own.
    expect(declaration(touch, 'font-size')).toBe('var(--control-text)')
  })

  it('sizes every text-entry control off that one token, missing none of them', () => {
    const componentRule = (path: string, selector: string) =>
      declaration(ruleBlock(readFileSync(`${process.cwd()}/${path}`, 'utf8'), selector), 'font-size')

    expect(declaration(ruleBlock(readBase(), '.field'), 'font-size')).toBe('var(--control-text)')
    expect(
      componentRule('resources/js/components/atoms/NumberStepper.vue', '.stepper-input'),
    ).toBe('var(--control-text)')
    expect(
      componentRule('resources/js/components/atoms/SearchInput.vue', '.search-input-field'),
    ).toBe('var(--control-text)')
  })

  it('leaves pinch-zoom alone, which is the other way to stop that zoom', () => {
    // Barring it would take the reader's own zoom with it (WCAG 1.4.4).
    const html = readFileSync(`${process.cwd()}/index.html`, 'utf8')

    expect(html).not.toMatch(/maximum-scale|user-scalable/)
  })
})

describe('the items a recommendation cites', () => {
  it('lists them one to a row on a phone, however long the titles run', () => {
    const mobile = mediaBlock(readBase(), '640px')

    expect(declaration(ruleBlock(mobile, '.rec-evidence'), 'flex-direction')).toBe('column')
    expect(declaration(ruleBlock(mobile, '.rec-evidence'), 'align-items')).toBe('flex-start')
  })

  it('wraps a long title rather than clipping it to fit', () => {
    const badge = ruleBlock(readBase(), '.badge--wrap')

    expect(declaration(badge, 'white-space')).toBe('normal')
    expect(badge).not.toMatch(/text-overflow:/)
  })
})

describe('full-bleed controls inside a bordered field', () => {
  // The ring is offset outward and the controls are full-bleed, so a clipping
  // parent leaves them with no focus indicator at all (WCAG 2.4.7).
  it('lets the shared focus ring paint outside the field that holds the tag entry', () => {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/molecules/SourceConfigForm.vue`,
      'utf8',
    )

    expect(declaration(ruleBlock(readBase(), ':focus-visible'), 'outline-offset')).not.toMatch(/^-/)
    expect(ruleBlock(source, '.chips-field')).not.toMatch(/overflow:\s*hidden/)
  })

  it('draws every focusable in the clipping stepper a ring the clip keeps', () => {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/atoms/NumberStepper.vue`,
      'utf8',
    )

    for (const selector of ['.stepper-btn:focus-visible', '.stepper-input:focus-visible']) {
      expect(declaration(ruleBlock(source, selector), 'outline-offset')).toMatch(/^-/)
    }
  })

  it('sizes the stepper to the number it holds, never to the column of fields', () => {
    const width = declaration(
      ruleBlock(
        readFileSync(`${process.cwd()}/resources/js/components/atoms/NumberStepper.vue`, 'utf8'),
        '.number-stepper',
      ),
      'width',
    )

    expect(width).toMatch(/^min\(.*100%\)$/)
  })
})

describe('.sr-only utility', () => {
  it('disables text selection so hidden labels never enter a copy', () => {
    // A loose /user-select:\s*none/ would match the `-webkit-` line as a
    // substring and so pass even if the unprefixed declaration were dropped, so
    // assert each explicitly. The negative lookbehind isolates the unprefixed
    // declaration.
    const source = readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
    const block = srOnlyBlock(source)
    expect(block).toMatch(/-webkit-user-select:\s*none/)
    expect(block).toMatch(/(?<!-)user-select:\s*none/)
  })
})

const DIALOG = 'resources/js/components/atoms/ModalDialog.vue'

function styledFiles(directory: string, kinds = /\.(css|vue)$/): string[] {
  return readdirSync(`${process.cwd()}/${directory}`, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? styledFiles(`${directory}/${entry.name}`, kinds)
      : kinds.test(entry.name)
        ? [`${directory}/${entry.name}`]
        : [],
  )
}

function stackingDeclarations(): [string, string][] {
  return styledFiles('resources').flatMap((path) =>
    [
      ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(
        /(?<![-\w])z-index:\s*([^;}"']+)/g,
      ),
    ].map(([, value]): [string, string] => [path, value.trim()]),
  )
}

function scale(source: string): Map<string, number> {
  return new Map(
    [...source.matchAll(/(--z-[\w-]+):\s*(\d+);/g)].map(([, name, value]) => [name, Number(value)]),
  )
}

function step(steps: Map<string, number>, value: string): number {
  const named = value.match(/^var\((--z-[\w-]+)\)$/)
  if (!named) throw new Error(`${value} is not a step on the scale`)
  const height = steps.get(named[1])
  if (height === undefined) throw new Error(`${named[1]} is declared nowhere`)
  return height
}

describe('library filter row (issue #102)', () => {
  // 1.4.10: a select cannot shrink past its widest option, so three of them
  // widened the page itself.
  it('lets the filter selects share a row instead of widening the page', () => {
    const mobile = mediaBlock(
      readFileSync(`${process.cwd()}/resources/js/components/organisms/LibraryFilters.vue`, 'utf8'),
      '640px',
    )
    const select = ruleBlock(mobile, '.lib-filter-row .toolbar-select')

    expect(declaration(select, 'flex')).toContain('50%')
    expect(declaration(select, 'min-width')).toBe('0')
  })
})

describe('stacking order', () => {
  it('leaves no raw z-index and no unused step, so one scale decides what covers what', () => {
    const declared = [...scale(readBase()).keys()]
    const found = stackingDeclarations()
    const used = new Set(found.map(([, value]) => value))

    expect(found.filter(([, value]) => !/^var\(--z-[\w-]+\)$/.test(value))).toEqual([])
    expect(declared.filter((name) => !used.has(`var(${name})`))).toEqual([])
  })

  it('puts an open dialog over every other surface, the mobile drawer and its toggle included', () => {
    const steps = scale(readBase())
    const source = readFileSync(`${process.cwd()}/${DIALOG}`, 'utf8')
    const dialog = step(steps, declaration(ruleBlock(source, '.dialog-backdrop'), 'z-index'))
    const behind = stackingDeclarations().filter(([path]) => path !== DIALOG)

    expect(behind.length).toBeGreaterThan(0)
    for (const [path, value] of behind) {
      expect(dialog, `${path} draws over an open dialog`).toBeGreaterThan(step(steps, value))
    }
  })
})

describe('the bypass link', () => {
  // Equal z-index breaks on tree order, and the link is first in the tree
  // precisely so Tab reaches it first — which puts it under the chrome it
  // opens on top of.
  it('is painted over the rail and the top strip it lands on', () => {
    const source = readBase()
    const steps = scale(source)
    const link = step(steps, declaration(ruleBlock(source, '.skip-link'), 'z-index'))

    for (const chrome of ['.app-nav', '.app-topbar']) {
      expect(link, `${chrome} is drawn over the focused bypass link`).toBeGreaterThan(
        step(steps, declaration(ruleBlock(source, chrome), 'z-index')),
      )
    }
  })
})

const APP = 'resources/js/App.vue'

const SHELL_RULES = [
  '.app-shell',
  '.app-nav',
  '.nav-list',
  '.nav-item',
  '.nav-user',
  '.app-topbar',
  '.app-stage',
]

function restyles(body: string, selector: string): boolean {
  return new RegExp(`^\\s*${selector.replace(/\./g, '\\.')}[\\s{[]`, 'm').test(body)
}

function shellBreakpoints(): [string, string][] {
  return [
    ...readBase().matchAll(/@media \(max-width: ([^)]+)\)\s*\{([\s\S]*?)\n\}/g),
  ].flatMap(([, width, body]): [string, string][] =>
    SHELL_RULES.some((rule) => restyles(body, rule)) ? [[width, body]] : [],
  )
}

function fontFaceBlocks(source: string): string[] {
  return [...source.matchAll(/@font-face\s*\{([^}]*)\}/g)].map(([, body]) => body)
}

describe('the faces the app paints with', () => {
  it('fetches every one from this origin, so no page load reaches a font CDN', () => {
    const faces = styledFiles('resources').flatMap((path) =>
      fontFaceBlocks(readFileSync(`${process.cwd()}/${path}`, 'utf8')),
    )
    const remote = faces.flatMap((body) =>
      [...body.matchAll(/url\(\s*['"]?([^'")]+)/g)].flatMap(([, href]) =>
        /^(https?:)?\/\//.test(href) ? [href] : [],
      ),
    )

    expect(faces.length).toBeGreaterThan(0)
    expect(remote).toEqual([])
    expect(readFileSync(`${process.cwd()}/index.html`, 'utf8')).not.toMatch(
      /<link[^>]+href=['"](https?:)?\/\//,
    )
  })

  it('declares every face it swaps in, so no text waits on a blocking fetch', () => {
    for (const body of fontFaceBlocks(readBase())) {
      expect(body).toMatch(/font-display:\s*swap/)
    }
  })

  // The app vendors no mono face, so a rule asking for one lands on whatever
  // the reader's OS calls monospace.
  it('asks for no monospace anywhere, so a value is tabular sans or nothing', () => {
    const scanned = styledFiles('resources')
    const asking = scanned.flatMap((path) =>
      /monospace|--font-mono/.test(readFileSync(`${process.cwd()}/${path}`, 'utf8'))
        ? [path]
        : [],
    )

    expect(scanned.length).toBeGreaterThan(0)
    expect(asking).toEqual([])
  })
})

/** Every depth a stylesheet paints. An `inset` shadow is a hairline rule
 *  rather than a lift, so it names an edge token and no rung. */
function depths(): [string, string][] {
  return styledFiles('resources').flatMap((path) =>
    [
      ...readFileSync(`${process.cwd()}/${path}`, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .matchAll(/(?<![-\w])box-shadow:\s*([^;}]+)/g),
    ].map(([, value]): [string, string] => [path, value.trim()]),
  )
}

const RUNG = /^var\(--elevation-[0-3]\)$/

describe('the elevation ladder', () => {
  it('leaves no surface picking a depth of its own', () => {
    const painted = depths()
    const offLadder = painted.filter(
      ([, value]) =>
        !RUNG.test(value) &&
        !value.split(',').every((layer) => layer.trim().startsWith('inset')),
    )

    expect(painted.length).toBeGreaterThan(0)
    expect(offLadder).toEqual([])
  })

  it('leaves every lifted rung retunable by the palette that paints it', () => {
    const root = readBase().match(/:root\s*\{([\s\S]*?)\n\s*\}/)
    if (!root) throw new Error('no :root block in base.css')
    const palette = themePalette('nord')
    const rungs = [...root[1].matchAll(/--elevation-\d+:\s*([^;]+);/g)].map(([, value]) =>
      value.trim(),
    )

    expect(rungs.length).toBeGreaterThan(0)
    expect(
      rungs.filter((rung) => {
        const alias = rung.match(/^var\((--shadow-[\w-]+)\)$/)
        return alias === null ? rung !== 'none' : !palette.has(alias[1])
      }),
    ).toEqual([])
  })
})

describe('motion the reader asked not to see', () => {
  it('stops every animation in the app, wherever it is declared (WCAG 2.3.3)', () => {
    // Per-rule opt-outs are what rot: the next animation added — a spinner, a
    // skeleton — is covered by this one block or by nobody.
    const block = readBase().match(
      /@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/,
    )
    if (!block) throw new Error('no prefers-reduced-motion block in base.css')
    const animated = styledFiles('resources').flatMap((path) => [
      ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(
        /(?<![-\w])animation:\s*([^;]+);/g,
      ),
    ])

    expect(animated.length).toBeGreaterThan(1)
    expect(block[1]).toMatch(/^\s*\*,/m)
    expect(block[1]).toMatch(/animation-duration:[^;]*!important/)
    expect(block[1]).toMatch(/transition-duration:[^;]*!important/)
  })
})

function tokenDeclarations(paths: string[]): Set<string> {
  return new Set(
    paths.flatMap((path) => [
      ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(
        /(--[\w-]+)['"]?\s*:/g,
      ),
    ]).map(([, name]) => name),
  )
}

function shippedThemes(): string[] {
  return readdirSync(`${process.cwd()}/src/web/static/themes`, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
}

function themePalette(theme: string): Set<string> {
  return tokenDeclarations([`src/web/static/themes/${theme}/colors.css`])
}

describe('the token contract', () => {
  it('declares every token a stylesheet asks for, so none resolves to nothing', () => {
    const scanned = styledFiles('resources')
    const themes = shippedThemes().map((name) => `src/web/static/themes/${name}/colors.css`)
    const declared = tokenDeclarations([...scanned, ...themes])
    const orphaned = scanned.flatMap((path) =>
      [
        ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(/var\((--[\w-]+)/g),
      ].flatMap(([, name]) => (declared.has(name) ? [] : [`${path}: ${name}`])),
    )

    expect(scanned.length).toBeGreaterThan(0)
    expect(orphaned).toEqual([])
  })

  it('lets no theme reach past colour into a size the layout depends on', () => {
    // The contract is what nord declares, plus the non-colour tokens a theme
    // is allowed. --elevation-* is core because it derives from --shadow-*,
    // and a theme setting both leaves two knobs for one depth.
    const contract = themePalette('nord')
    const allowed = /^--(font-(ui|display)|radius-|shadow-)/

    expect(contract.size).toBeGreaterThan(0)
    for (const theme of shippedThemes()) {
      expect(
        [...themePalette(theme)].filter((name) => !contract.has(name) && !allowed.test(name)),
        theme,
      ).toEqual([])
    }
  })

  it('is overridden in full by every shipped theme, so no grey shows through one', () => {
    // The fallback greys exist for a colors.css that declares half a palette.
    // A shipped theme that leaves one showing is a grey patch nothing reports.
    const root = readBase().match(/:root\s*\{([\s\S]*?)\n\s*\}/)
    if (!root) throw new Error('no :root block in base.css')
    const unbranded = [...root[1].matchAll(/(--[\w-]+):\s*([^;]+);/g)]
      .filter(([, , value]) => /#[0-9a-f]{3,8}\b|rgba?\(/i.test(value))
      .map(([, name]) => name)

    expect(unbranded.length).toBeGreaterThan(0)
    for (const theme of shippedThemes()) {
      const declared = themePalette(theme)
      expect(unbranded.filter((name) => !declared.has(name)), theme).toEqual([])
    }
  })
})

describe('the heights the chrome reserves', () => {
  // A px constant does not move when the chrome itself grows with the text, and
  // the shell lays every page out inside what these two reserve.
  it('grows every reserved chrome height with the text in it (WCAG 1.4.4)', () => {
    const reserved = [...readBase().matchAll(/(--[\w-]+-h):\s*([^;]+);/g)]

    expect(reserved.length).toBeGreaterThanOrEqual(2)
    expect(
      reserved.flatMap(([, name, value]) => (/var\(--text-/.test(value) ? [] : [name])),
    ).toEqual([])
  })
})

describe('the settings column', () => {
  it('caps every control it renders at the card, whatever type the leaf is', () => {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/molecules/SettingControl.vue`,
      'utf8',
    ).replace(/\/\*[\s\S]*?\*\//g, '')
    const widths = [...source.matchAll(/(?<![-\w])width:\s*([^;]+);/g)].map(([, value]) =>
      value.trim(),
    )

    expect(widths.length).toBeGreaterThan(0)
    expect(widths.filter((width) => !width.includes('100%'))).toEqual([])
  })
})

describe('a control with a floor measured in rem', () => {
  it('caps that floor at the column holding it, so a text zoom cannot widen the page', () => {
    const scanned = styledFiles('resources')
    const uncapped = scanned.flatMap((path) =>
      [
        ...readFileSync(`${process.cwd()}/${path}`, 'utf8')
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .matchAll(/(?<![-\w])min-width:\s*([^;]+);/g),
      ].flatMap(([, value]) =>
        /[\d.]rem\b/.test(value) && !value.includes('min(') ? [`${path}: ${value.trim()}`] : [],
      ),
    )

    expect(scanned.length).toBeGreaterThan(0)
    expect(uncapped).toEqual([])
  })
})

describe('the score spine', () => {
  // On hue alone the penalty reads as more of the score it came off.
  it('separates its tones by a channel that is not colour (WCAG 1.4.11)', () => {
    const source = readBase()
    const lead = ruleBlock(source, '.rec-spine-segment')
    const rest = ruleBlock(source, '.rec-spine-segment--rest')
    const penalty = ruleBlock(source, '.rec-spine-segment--penalty')

    expect(declaration(rest, 'height')).not.toBe(declaration(lead, 'height'))
    expect(declaration(penalty, 'background-image')).toContain('repeating-linear-gradient')
  })
})

describe('the rail at a text size taller than the viewport', () => {
  it('scrolls the tabs it cannot fit rather than stranding them below the edge', () => {
    const rail = ruleBlock(readBase(), '.app-nav')

    expect(declaration(rail, 'height')).toMatch(/^100/)
    expect(declaration(rail, 'overflow-y')).toBe('auto')
  })
})

describe('the rail becoming a tab bar', () => {
  // Two queries and a script watching a third let an 800px tablet have the nav
  // off screen at a width nothing called narrow, so it stayed tabbable while
  // invisible. One query, and no script, is what makes that unreachable.
  it('turns the shell over at one width, decided by the stylesheet alone', () => {
    const widths = shellBreakpoints().map(([width]) => width)

    expect(widths.length).toBeGreaterThan(0)
    expect([...new Set(widths)]).toEqual(widths.slice(0, 1))
    expect(readFileSync(`${process.cwd()}/${APP}`, 'utf8')).not.toMatch(/@media|matchMedia/)
  })

  // The drawer had to go inert because it was on screen and hidden at once.
  // The tab bar owes the same promise by never being hidden at all.
  it('leaves the nav and every tab on screen at that width', () => {
    const [[, narrow]] = shellBreakpoints()

    for (const rule of ['.app-nav', '.nav-list', '.nav-item']) {
      expect(narrow, `${rule} is hidden on a phone`).not.toMatch(
        new RegExp(`${rule.replace(/\./g, '\\.')}\\s*\\{[^}]*display:\\s*none`),
      )
    }
  })

  // Sticky would scroll the tabs away; taking them out of the flow instead
  // costs the shell the height back, or every page ends under them.
  it('pays back the height a detached tab bar stops occupying', () => {
    const [[, narrow]] = shellBreakpoints()
    const detached = /position:\s*(?:fixed|absolute)/.test(ruleBlock(narrow, '.app-nav'))
    const reserved = /padding-bottom:|margin-bottom:/.test(ruleBlock(narrow, '.app-shell'))

    expect(reserved || !detached, 'the tab bar covers the bottom of every page').toBe(true)
  })
})
