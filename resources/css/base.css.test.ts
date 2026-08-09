import { readFileSync } from 'node:fs'
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
