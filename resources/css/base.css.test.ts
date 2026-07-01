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
