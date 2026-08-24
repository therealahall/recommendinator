import { readFileSync, readdirSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// base.css is a static asset: importing it through Vite yields an empty stub
// under Vitest, so read the file off disk to assert on its real contents.

// Isolate the `.sr-only { ... }` declaration block so the assertion cannot be
// satisfied by an unrelated rule that happens to mention user-select. This
// assumes `.sr-only` is a standalone selector; if it is ever merged into a
// multi-selector rule the regex won't match and the test throws "not found",
// which is the correct fail-mode.
function srOnlyBlock(source: string): string {
  const match = source.match(/\.sr-only\s*\{([^}]*)\}/)
  if (!match) throw new Error('.sr-only rule not found in base.css')
  return match[1]
}

function readBase(): string {
  return readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
}

// Requiring `{` immediately after the selector keeps a lookup for
// `.library-meta` off `.library-meta-secondary` and off `.library-meta .badge`.
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

describe('inactive button styling', () => {
  // Regression: the project had NO `.btn:disabled` rule at all. `.btn` sets a
  // solid background and `cursor: pointer`, so every button locked during a
  // save — Reset, Add, each chip's ×, Replace/Clear/Save secret, and every
  // toggle — rendered pixel-identically to a working one and silently ignored
  // clicks. Native inputs and selects get UA greying; buttons get none, so the
  // in-flight lock was conveyed to assistive tech and to nobody else (1.3.1).
  //
  // Asserted here rather than per component because the whole point is that one
  // shared rule covers every button; a per-component test would pass while the
  // next new button shipped unstyled.
  it('dims and re-cursors both inactive spellings', () => {
    const source = readBase()
    const match = source.match(
      /\.btn:disabled,\s*\.btn\[aria-disabled='true'\],\s*\.toggle-switch:disabled\s*\{([^}]*)\}/,
    )
    if (!match) throw new Error('shared inactive-button rule not found in base.css')

    expect(match[1]).toMatch(/opacity:/)
    expect(match[1]).toMatch(/cursor:\s*not-allowed/)
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
  // These three read as dead resets, so a tidy-up deletes them — and each is
  // the only rule covering its surface, so nothing else fails when it goes.
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
    // Browsers pull visually-clipped text into a selection, so copying an
    // on-screen value next to an sr-only label would paste the hidden words.
    // `user-select: none` is the root-level guard against that defect.
    //
    // Require BOTH the standard and the `-webkit-` declarations: the standard
    // one covers Chrome/Firefox, the prefixed one covers Safari. A loose
    // /user-select:\s*none/ would match the `-webkit-` line as a substring and
    // so pass even if the unprefixed declaration were dropped, so assert each
    // explicitly. The negative lookbehind isolates the unprefixed declaration.
    const source = readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8')
    const block = srOnlyBlock(source)
    expect(block).toMatch(/-webkit-user-select:\s*none/)
    expect(block).toMatch(/(?<!-)user-select:\s*none/)
  })
})

describe('library card divider (issue #108)', () => {
  /**
   * Bug: on a one-column mobile grid the divider above the buttons touched the
   * badges, but looked right whenever the card was rated. Root cause:
   * margin-top:auto is zero on a content-height card. Fix: margin on the pills.
   */
  it('spaces the badges off the divider whether or not the card is rated', () => {
    const source = readBase()
    const gap = declaration(ruleBlock(source, '.library-meta'), 'margin-bottom')

    expect(gap).not.toBe('0')
    expect(declaration(ruleBlock(source, '.library-meta-secondary'), 'margin-bottom')).toBe(gap)
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

describe('recommendation card header (issue #98)', () => {
  /**
   * Bug: at 375px the score badge and buttons squeezed the title. Root cause:
   * .rec-header is a no-wrap row and the title wrapper had no class to hang a
   * basis on. Fix: wrap the header below 640px.
   */
  it('gives the title the whole row and drops the actions beneath it', () => {
    const block = mediaBlock(readBase(), '640px')

    expect(declaration(ruleBlock(block, '.rec-header'), 'flex-wrap')).toBe('wrap')
    expect(declaration(ruleBlock(block, '.rec-heading'), 'flex')).toBe('1 1 100%')
    expect(declaration(ruleBlock(block, '.rec-heading'), 'min-width')).toBe('0')
    expect(declaration(ruleBlock(block, '.rec-actions'), 'width')).toBe('100%')
  })
})
