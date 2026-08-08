import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TokenGate from './TokenGate.vue'
import { useAuthStore } from '@/stores/auth'

describe('TokenGate', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('stores what was submitted, which is what unblocks the app', async () => {
    const wrapper = mount(TokenGate)

    await wrapper.find('#token-gate-input').setValue('the-token')
    await wrapper.find('form').trigger('submit')

    expect(useAuthStore().token).toBe('the-token')
  })

  it('will not submit an empty token', async () => {
    const wrapper = mount(TokenGate)

    await wrapper.find('form').trigger('submit')

    expect(useAuthStore().isAuthenticated).toBe(false)
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('masks the field, since a token is a credential', () => {
    const wrapper = mount(TokenGate)

    expect(wrapper.find('#token-gate-input').attributes('type')).toBe('password')
  })

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mount(TokenGate)

    const region = wrapper.find('#token-gate-error')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('announces a refusal through that same region', async () => {
    const wrapper = mount(TokenGate)

    useAuthStore().reject()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('#token-gate-error').text()).toContain('not accepted')
    expect(wrapper.find('#token-gate-input').attributes('aria-invalid')).toBe('true')
  })
})
