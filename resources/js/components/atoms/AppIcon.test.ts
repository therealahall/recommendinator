import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppIcon from './AppIcon.vue'

describe('AppIcon', () => {
  // Every glyph sits beside its own label or in a button carrying an
  // aria-label, so an announced icon reads twice — and a focusable <svg> adds
  // a tab stop on Edge.
  it('is decorative and unreachable by Tab', () => {
    const icon = mount(AppIcon, { props: { name: 'search' } })

    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(icon.attributes('focusable')).toBe('false')
  })

  it('takes its colour from the control it sits in, so a tone never strands it', () => {
    expect(mount(AppIcon, { props: { name: 'close' } }).attributes('stroke')).toBe('currentColor')
  })

  it('offers only the three sizes the layout is built on', () => {
    const at = (size: 16 | 20 | 40): string[] =>
      mount(AppIcon, { props: { name: 'menu', size } }).classes()

    expect(at(16)).toEqual(['icon'])
    expect(at(20)).toContain('icon--20')
    expect(at(40)).toContain('icon--40')
  })
})
