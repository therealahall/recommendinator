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
    const fixed = styledFiles('resources').flatMap((path) =>
      [
        ...readFileSync(`${process.cwd()}/${path}`, 'utf8')
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .matchAll(/(?<![-\w])font-size:\s*([^;]+);/g),
      ].flatMap(([, value]) => (/\d(px|pt)\b/.test(value) ? [`${path}: ${value.trim()}`] : [])),
    )

    expect(fixed).toEqual([])
  })
})

describe('the scorer tooltip', () => {
  it('opens inside a 320px viewport rather than off the side of it', () => {
    // 260px centred on its trigger hung half the box past the left edge of the
    // page, and nothing about it could shrink (WCAG 1.4.10).
    expect(declaration(ruleBlock(readBase(), '.scorer-tooltip-text'), 'transform')).toBe('none')
    expect(declaration(ruleBlock(readBase(), '.scorer-tooltip-text'), 'max-width')).toContain('100vw')
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

describe('recommendation card header (issue #98)', () => {
  // 1.4.10: at 375px the score badge and buttons squeezed the title until the
  // card scrolled sideways.
  it('gives the heading its own row before the actions crowd it', () => {
    const mobile = mediaBlock(readBase(), '640px')

    expect(declaration(ruleBlock(mobile, '.rec-header'), 'flex-wrap')).toBe('wrap')
    expect(declaration(ruleBlock(mobile, '.rec-heading'), 'flex')).toBe('1 1 100%')
    expect(declaration(ruleBlock(mobile, '.rec-heading'), 'min-width')).toBe('0')
    expect(declaration(ruleBlock(mobile, '.rec-actions'), 'width')).toBe('100%')
  })
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

function styledFiles(directory: string): string[] {
  return readdirSync(`${process.cwd()}/${directory}`, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? styledFiles(`${directory}/${entry.name}`)
      : /\.(css|vue)$/.test(entry.name)
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
  // precisely so Tab reaches it before the toggle — which puts it underneath.
  it('is not painted under the drawer toggle it shares a corner with', () => {
    const source = readBase()
    const toggle = ruleBlock(source, '.sidebar-toggle')
    const link = ruleBlock(source, '.skip-link')
    const corner = (top: string, left: string) => `${top} ${left}`
    const covered =
      corner(declaration(ruleBlock(source, '.skip-link:focus'), 'top'), declaration(link, 'left')) ===
        corner(declaration(toggle, 'top'), declaration(toggle, 'left')) &&
      step(scale(source), declaration(link, 'z-index')) <=
        step(scale(source), declaration(toggle, 'z-index'))

    expect(covered, 'the focused bypass link is drawn under the drawer toggle').toBe(false)
  })
})

const APP = 'resources/js/App.vue'

function drawerBreakpoints(): string[] {
  return ['resources/css/base.css', APP].flatMap((path) =>
    [
      ...readFileSync(`${process.cwd()}/${path}`, 'utf8').matchAll(
        /@media \(max-width: ([^)]+)\)\s*\{([\s\S]*?)\n\}/g,
      ),
    ].flatMap(([, width, body]) => (/\.sidebar\s*\{[^}]*left:/.test(body) ? [width] : [])),
  )
}

describe('the mobile sidebar drawer', () => {
  // Widen the CSS alone and an 800px tablet has the sidebar off screen with
  // isNarrow false, so it never goes inert and Tab walks six invisible buttons.
  it('goes inert at the width the CSS slides it off screen', () => {
    const watched = readFileSync(`${process.cwd()}/${APP}`, 'utf8').match(
      /matchMedia\('\(max-width: ([^)]+)\)'\)/,
    )
    if (!watched) throw new Error('App.vue watches no viewport width')
    const breakpoints = drawerBreakpoints()

    expect(breakpoints.length).toBeGreaterThan(0)
    expect([...new Set(breakpoints)]).toEqual([watched[1]])
  })
})
