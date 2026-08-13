import { readdirSync, readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import { globalFocusRing } from '@/testing/focusRing'

// base.css declares one focus ring, offset and measured against the surfaces
// it lands on. A component that cancels it substitutes something nobody
// measured — which is how six fields shipped a 1.55:1 tint (WCAG 1.4.11).

const VUE_ROOT = `${process.cwd()}/resources/js`
const STYLE_BLOCK = /<style[^>]*>([\s\S]*?)<\/style>/g
// Stripped before the rules are read: a comment naming :focus-visible would
// otherwise be swept into the selector of whatever rule follows it.
const COMMENT = /\/\*[\s\S]*?\*\//g
const RULE = /([^{}]+)\{([^{}]*)\}/g
// Every spelling that erases the ring, not just `none`: a zero width and a
// transparent colour leave a keyboard user with exactly as little.
const CANCELS = /outline:\s*(none|0)\b|outline-width:\s*0|outline-color:\s*transparent/

function vueSources(): [string, string][] {
  return readdirSync(VUE_ROOT, { recursive: true, encoding: 'utf8' })
    .filter((entry) => entry.endsWith('.vue'))
    .map((entry): [string, string] => [entry, readFileSync(`${VUE_ROOT}/${entry}`, 'utf8')])
}

function rules(source: string): [string, string][] {
  const styles = Array.from(source.matchAll(STYLE_BLOCK), (match) => match[1])
    .join('\n')
    .replace(COMMENT, '')

  return Array.from(styles.matchAll(RULE)).map(([, selector, block]): [string, string] => [
    selector.trim(),
    block,
  ])
}

/** The ring may move to an ancestor wrapping the control, which is how
 *  SearchInput rings the whole field rather than the bare <input>. */
function delegatedRings(source: string): [string, string][] {
  return rules(source).filter(
    ([selector, block]) => selector.includes(':has(:focus-visible)') && !CANCELS.test(block),
  )
}

/** Rules styling the focused element itself — the only ones able to erase its
 *  own ring. `:focus-within` and `:has()` match an ancestor, and the
 *  `:not(:focus-visible)` rules are the pointer-only suppression the ring
 *  depends on. */
function ownFocusRules(source: string): [string, string][] {
  return rules(source).filter(
    ([selector]) =>
      selector.includes(':focus') &&
      !selector.includes(':not(:focus-visible)') &&
      !selector.includes(':focus-within') &&
      !selector.includes(':has('),
  )
}

/** The element a delegated ring is drawn on: `.search-input` out of
 *  `.search-input:has(:focus-visible)`. */
function ringHost(selector: string): string {
  return selector.slice(0, selector.indexOf(':has(')).trim()
}

/** A cancellation is excused only by a host its own selector starts with —
 *  `.search-input-field` under `.search-input`, which is how a wrapper and its
 *  parts are named here. A delegated ring elsewhere in the file excuses none. */
function cancelledRings(source: string): string[] {
  const hosts = delegatedRings(source).map(([selector]) => ringHost(selector))

  return ownFocusRules(source)
    .filter(
      ([selector, block]) =>
        CANCELS.test(block) && !hosts.some((host) => host !== '' && selector.startsWith(host)),
    )
    .map(([selector]) => selector)
}

describe('the one focus ring', () => {
  it('is cancelled by no component that draws nothing in its place', () => {
    const sources = vueSources()
    // App.vue carries an unscoped <style> and sits outside components/, so the
    // audit reads every .vue the app ships rather than the component tree.
    expect(sources.map(([name]) => name)).toContain('App.vue')

    let audited = 0
    const cancelled: string[] = []
    for (const [name, source] of sources) {
      audited += ownFocusRules(source).length
      cancelled.push(...cancelledRings(source).map((selector) => `${name}: ${selector}`))
    }

    expect(audited, 'no focus rule was read, so nothing was audited').toBeGreaterThan(0)
    expect(cancelled).toEqual([])
  })

  it('excuses only the part whose own wrapper draws the ring', () => {
    // Per file, one delegated ring anywhere excused every cancellation in the
    // same file — including one on an element it does not contain.
    const source = `<style scoped>
      .wrap:has(:focus-visible) { outline: 2px solid var(--accent-light); outline-offset: 2px; }
      .wrap-field:focus-visible { outline: none; }
      .elsewhere input:focus-visible { outline: none; }
    </style>`

    expect(cancelledRings(source)).toEqual(['.elsewhere input:focus-visible'])
  })

  it.each(['outline: none;', 'outline: 0;', 'outline-width: 0;', 'outline-color: transparent;'])(
    'reads `%s` as a ring nobody redrew',
    (declaration) => {
      // The audit above is green either because the components draw the ring
      // or because the reader cannot see the rule that erases it. This is what
      // separates the two.
      const source = `<style scoped>
        /* :focus-visible named in a comment is not a selector */
        .field input:focus-visible { ${declaration} }
      </style>`

      expect(ownFocusRules(source).map(([selector]) => selector)).toEqual([
        '.field input:focus-visible',
      ])
      expect(CANCELS.test(ownFocusRules(source)[0][1])).toBe(true)
    },
  )

  it('leaves the rules that cancel nothing of the focused element alone', () => {
    // Both spellings are load-bearing: the first is the pointer-only
    // suppression base.css relies on, the second rings an ancestor.
    const source = `<style scoped>
      input:focus:not(:focus-visible) { outline: none; }
      .wrap:focus-within { outline: none; }
      .wrap:has(:focus-visible) { outline: 2px solid var(--accent-light); outline-offset: 2px; }
    </style>`

    expect(ownFocusRules(source)).toEqual([])
    expect(delegatedRings(source).map(([selector]) => selector)).toEqual([
      '.wrap:has(:focus-visible)',
    ])
  })

  it('draws the base ring wherever it is delegated, never a copy that can drift', () => {
    // Only base.css's ring is measured for contrast, so a delegated ring that
    // restates the colour or the offset is an unmeasured second indicator.
    const ring = globalFocusRing()
    const delegated = vueSources().flatMap(([name, source]) =>
      delegatedRings(source).map(([selector, block]): [string, string] => [
        `${name}: ${selector}`,
        block,
      ]),
    )

    expect(delegated.length, 'no delegated ring found, so nothing was compared').toBeGreaterThan(0)
    for (const [where, block] of delegated) {
      expect(block, where).toContain(`solid ${ring.color}`)
      expect(block, where).toMatch(new RegExp(`outline-offset:\\s*${ring.offsetPx}px`))
    }
  })
})
