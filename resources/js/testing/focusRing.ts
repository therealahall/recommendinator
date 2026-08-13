import { readFileSync } from 'node:fs'

/** The one ring base.css declares. Read rather than restated, so an assertion
 *  cannot keep passing against a colour or offset the app stopped shipping. */
export function globalFocusRing(): { color: string; offsetPx: number } {
  const rule = readFileSync(`${process.cwd()}/resources/css/base.css`, 'utf8').match(
    /^:focus-visible\s*\{([^}]*)\}/m,
  )
  if (!rule) throw new Error('global :focus-visible rule not found in base.css')

  const outline = rule[1].match(/outline:\s*\d+px\s+solid\s+(var\(--[a-z0-9-]+\))/)
  const offset = rule[1].match(/outline-offset:\s*(-?\d+)px/)
  if (!outline || !offset) throw new Error('the focus ring declares no solid outline and offset')

  return { color: outline[1], offsetPx: Number(offset[1]) }
}
