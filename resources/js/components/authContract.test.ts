import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// Props in, events out: their parents own the routes, so a store import or a
// hardcoded path here would put the wiring in two places.

const SURFACES = [
  'resources/js/components/atoms/AuthField.vue',
  'resources/js/components/molecules/AccountProfileForm.vue',
  'resources/js/components/molecules/PasswordChangeForm.vue',
  'resources/js/components/organisms/AccountSection.vue',
  'resources/js/components/organisms/LoginForm.vue',
  'resources/js/components/organisms/SetupForm.vue',
]

const STYLED = [...SURFACES, 'resources/js/components/organisms/AppSidebar.vue']

/** Every form's submit button, which locks with aria-disabled: native disabled
 *  unfocuses the element it lands on, and each of these flips on the outcome
 *  the user is most likely to reach it by (WCAG 2.4.3). */
const SUBMITTERS = [
  'resources/js/components/molecules/AccountProfileForm.vue',
  'resources/js/components/molecules/PasswordChangeForm.vue',
  'resources/js/components/organisms/LoginForm.vue',
  'resources/js/components/organisms/SetupForm.vue',
]

const PREFIX =
  '(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|min|max|gap|space|text|bg|border|rounded|shadow|font|leading|tracking|grid|col|row|inset|top|bottom|left|right|z|opacity|ring|divide|order|basis|flex|items|justify|self|overflow|cursor)'
// Tailwind's value vocabulary. Requiring one is what stops `row-actions` and
// `top-bar` — project class names — from reading as `row-2` and `top-0`.
const VALUE =
  '(?:\\d[\\d./]*|\\[[^\\]]+\\]|px|auto|full|screen|fit|none|xs|sm|md|lg|\\d?xl|white|black|transparent|current|bold|semibold|medium|normal|light|left|right|center|start|end|between|around|evenly|baseline|stretch|col|row|wrap|nowrap|reverse|pointer|solid|dashed|dotted)'
const BARE =
  '(?:flex|grid|block|inline|hidden|absolute|relative|fixed|sticky|truncate|container|uppercase|lowercase|capitalize|italic|underline)'

// Utilities, not the project's semantic class names: a `p-4` or a `w-full` here
// puts spacing where base.css cannot theme it or a theme cannot override it.
const UTILITY = new RegExp(`^(?:-?${PREFIX}-(?:[a-z]+-)*${VALUE}|${BARE})$`)

function source(path: string): string {
  return readFileSync(`${process.cwd()}/${path}`, 'utf8')
}

function staticClasses(text: string): string[] {
  return [...text.matchAll(/\sclass="([^"]*)"/g)].flatMap(([, value]) => value.split(/\s+/)).filter(Boolean)
}

describe('account surface contract', () => {
  it('covers every component of the three surfaces', () => {
    expect(SURFACES).toHaveLength(6)
    for (const path of SURFACES) expect(source(path)).not.toBe('')
  })

  it('reads a utility as a utility and a project class name as neither', () => {
    // The scan below is green either because the surfaces are clean or because
    // the pattern matches nothing. This is what separates the two — and the
    // second list is what the pattern used to fire on.
    for (const utility of ['p-4', 'w-full', 'mt-2', 'text-sm', 'gap-1.5', 'w-[12px]', 'min-w-0', 'flex', 'truncate']) {
      expect(UTILITY.test(utility), utility).toBe(true)
    }
    for (const name of ['auth-card', 'auth-field-hint', 'row-actions', 'top-bar', 'text-muted', 'items-list', 'order-history', 'min-height', 'sidebar-user-name']) {
      expect(UTILITY.test(name), name).toBe(false)
    }
  })

  it.each(SUBMITTERS)('%s locks its submit button without unfocusing it', (path) => {
    // Regression: each of these switched to native `disabled` one tick after
    // the request it had just sent came back, dropping focus to <body>.
    const text = source(path)

    expect(text).toMatch(/:aria-disabled="pending \|\| /)
    expect(text).not.toMatch(/\s:disabled=/)
  })

  it('introduces no Tailwind utilities, on the surfaces or in the sidebar', () => {
    const applied = STYLED.flatMap((path) => staticClasses(source(path)))

    expect(applied).toContain('auth-card')
    expect(applied).toContain('sidebar-user-name')
    expect(applied.filter((name) => UTILITY.test(name))).toEqual([])
  })

  it.each(SURFACES)('%s reaches no store, no fetch and no endpoint', (path) => {
    const text = source(path)

    expect(text).not.toMatch(/@\/stores\//)
    expect(text).not.toMatch(/useApi/)
    expect(text).not.toMatch(/\bfetch\s*\(/)
    expect(text).not.toMatch(/['"`]\/api\//)
  })
})
