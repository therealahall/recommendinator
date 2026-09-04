import { describe, it, expect } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SettingSecret from './SettingSecret.vue'
import type { SettingViewSecret } from '@/types/api'

function secret(overrides: Partial<SettingViewSecret> = {}): SettingViewSecret {
  return {
    key: 'enrichment.providers.tmdb.api_key',
    section: 'enrichment',
    label: 'TMDB API key',
    help: 'Used to reach the provider',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: true,
    has_secret: false,
    ...overrides,
  }
}

describe('SettingSecret', () => {
  it('reveals an empty password input on Replace and never prefills the value', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: true }) } })
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    const input = wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key')
    expect(input.attributes('type')).toBe('password')
    expect(input.attributes('autocomplete')).toBe('new-password')
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('emits set with the entered value on Save secret', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-123')
    const save = wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]')
    expect(save.attributes('aria-disabled')).toBeUndefined()
    await save.trigger('click')
    expect(wrapper.emitted('set')).toEqual([['sk-123']])
  })

  it('exposes Save secret as unavailable on an empty draft rather than swallowing the press', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    const save = wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]')
    expect(save.attributes('aria-disabled')).toBe('true')
    await save.trigger('click')
    expect(wrapper.emitted('set')).toBeUndefined()
  })

  it('emits clear on Clear', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: true }) } })
    await wrapper.find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]').trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
  })

  it('keeps Clear focusable while its own write is in flight, and drops the press', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }), busy: true },
    })
    const clear = wrapper.find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]')

    await clear.trigger('click')

    expect(clear.attributes('disabled')).toBeUndefined()
    expect(clear.attributes('aria-disabled')).toBe('true')
    expect(wrapper.emitted('clear')).toBeUndefined()
  })

  it('lands focus on Replace when Save secret closes the row', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret() },
      attachTo: document.body,
    })
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-123')

    await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').element,
    )
    wrapper.unmount()
  })

  it('leaves focus where the operator tabbed to when a write finishes', async () => {
    const elsewhere = document.createElement('button')
    document.body.appendChild(elsewhere)
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }), busy: true },
      attachTo: document.body,
    })
    elsewhere.focus()

    await wrapper.setProps({ busy: false })
    await flushPromises()

    expect(document.activeElement).toBe(elsewhere)
    wrapper.unmount()
    elsewhere.remove()
  })

  it('rescues focus to Replace when the write left it stranded on <body>', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }), busy: true },
      attachTo: document.body,
    })
    expect(document.activeElement).toBe(document.body)

    await wrapper.setProps({ busy: false })
    await flushPromises()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').element,
    )
    wrapper.unmount()
  })

  it('closes the input on Cancel without emitting set', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('partial')
    await wrapper.find('[data-testid="secret-cancel-enrichment.providers.tmdb.api_key"]').trigger('click')
    expect(wrapper.emitted('set')).toBeUndefined()
    expect(wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').exists()).toBe(false)
  })
})
