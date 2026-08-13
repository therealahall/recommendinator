import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// The routes these surfaces will be wired to do not exist yet, so a store
// import or a hardcoded path would have to be unpicked before they could be.

const SURFACES = [
  'resources/js/components/atoms/AuthField.vue',
  'resources/js/components/molecules/AccountProfileForm.vue',
  'resources/js/components/molecules/PasswordChangeForm.vue',
  'resources/js/components/organisms/AccountSection.vue',
  'resources/js/components/organisms/LoginForm.vue',
  'resources/js/components/organisms/SetupForm.vue',
]

const STYLED = [...SURFACES, 'resources/js/components/organisms/AppSidebar.vue']

// Utilities, not the project's semantic class names: a `p-4` or a `w-full` here
// puts spacing where base.css cannot theme it or a theme cannot override it.
const UTILITY =
  /^(?:-?(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|min|max|gap|space|text|bg|border|rounded|shadow|font|leading|tracking|grid|col|row|inset|top|bottom|left|right|z|opacity|ring|divide|order|basis|flex|items|justify|self|overflow|cursor)-[\w./[\]%-]+|flex|grid|block|inline|hidden|absolute|relative|fixed|sticky|truncate|container|uppercase|lowercase|capitalize|italic|underline)$/

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
