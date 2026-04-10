import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SyncSourceCard from './SyncSourceCard.vue'
import type { SyncSourceResponse } from '@/types/api'

const source: SyncSourceResponse = {
  id: 'steam',
  display_name: 'Steam',
  plugin_display_name: 'Steam Plugin',
}

function mountCard(props: { syncing: boolean; disabled?: boolean; showSyncButton?: boolean }) {
  return mount(SyncSourceCard, {
    props: { source, ...props },
  })
}

describe('SyncSourceCard', () => {
  it('renders source name and plugin name', () => {
    const wrapper = mountCard({ syncing: false })
    expect(wrapper.text()).toContain('Steam')
    expect(wrapper.text()).toContain('Steam Plugin')
  })

  it('button is enabled and shows "Sync" when not syncing and not disabled', () => {
    const wrapper = mountCard({ syncing: false, disabled: false })
    const btn = wrapper.find('.sync-btn')
    expect(btn.text()).toBe('Sync')
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('button is disabled and shows "Syncing..." when syncing', () => {
    const wrapper = mountCard({ syncing: true, disabled: false })
    const btn = wrapper.find('.sync-btn')
    expect(btn.text()).toBe('Syncing...')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('button is disabled with explanation when disabled but not syncing', () => {
    const wrapper = mountCard({ syncing: false, disabled: true })
    const btn = wrapper.find('.sync-btn')
    expect(btn.text()).toBe('Sync')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('aria-label')).toBe('Sync (another sync is in progress)')
  })

  it('button has no aria-label override when syncing', () => {
    const wrapper = mountCard({ syncing: true, disabled: false })
    const btn = wrapper.find('.sync-btn')
    expect(btn.attributes('aria-label')).toBeUndefined()
  })

  it('button is disabled and shows "Syncing..." when both syncing and disabled', () => {
    const wrapper = mountCard({ syncing: true, disabled: true })
    const btn = wrapper.find('.sync-btn')
    expect(btn.text()).toBe('Syncing...')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('hides sync button when showSyncButton is false', () => {
    const wrapper = mountCard({ syncing: false, showSyncButton: false })
    expect(wrapper.find('.sync-btn').exists()).toBe(false)
  })

  it('emits sync event with source id on click', async () => {
    const wrapper = mountCard({ syncing: false })
    await wrapper.find('.sync-btn').trigger('click')
    expect(wrapper.emitted('sync')).toEqual([['steam']])
  })

  it('does not emit sync event when button is disabled', async () => {
    const wrapper = mountCard({ syncing: false, disabled: true })
    await wrapper.find('.sync-btn').trigger('click')
    expect(wrapper.emitted('sync')).toBeUndefined()
  })
})
