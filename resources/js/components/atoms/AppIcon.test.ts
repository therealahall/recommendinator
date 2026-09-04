import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppIcon from './AppIcon.vue'

describe('AppIcon', () => {
  it('is decorative and unreachable by Tab', () => {
    const icon = mount(AppIcon, { props: { name: 'search' } })

    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(icon.attributes('focusable')).toBe('false')
  })

  it('takes its colour from the control it sits in, so a tone never strands it', () => {
    expect(mount(AppIcon, { props: { name: 'close' } }).attributes('stroke')).toBe('currentColor')
  })
})
