import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import SettingSecret from './SettingSecret.vue'
import type { SettingViewSecret } from '@/types/api'

// setProps infers the wrapper's bare VNode props, so a fresh `{ busy }` literal
// trips the excess-property check. Route each update through a variable of this
// type — the component's own props — which the assignment accepts cleanly.
type SecretProps = Partial<InstanceType<typeof SettingSecret>['$props']>

function secret(overrides: Partial<SettingViewSecret> = {}): SettingViewSecret {
  return {
    key: 'llm.api_key',
    section: 'llm',
    label: 'API Key',
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
  it('shows "Set" status and Replace + Clear when a secret exists', () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: true }) } })
    expect(wrapper.find('[data-testid="secret-status-llm.api_key"]').text()).toBe('Set')
    expect(wrapper.find('[data-testid="secret-replace-llm.api_key"]').text()).toBe('Replace')
    expect(wrapper.find('[data-testid="secret-clear-llm.api_key"]').exists()).toBe(true)
  })

  it('shows "Not set" status and a Set button without Clear when no secret exists', () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: false }) } })
    expect(wrapper.find('[data-testid="secret-status-llm.api_key"]').text()).toBe('Not set')
    expect(wrapper.find('[data-testid="secret-replace-llm.api_key"]').text()).toBe('Set')
    expect(wrapper.find('[data-testid="secret-clear-llm.api_key"]').exists()).toBe(false)
  })

  it('reveals an empty password input on Replace and never prefills the value', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: true }) } })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    const input = wrapper.find('#secret-input-llm\\.api_key')
    expect(input.attributes('type')).toBe('password')
    expect(input.attributes('autocomplete')).toBe('new-password')
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('emits set with the entered value on Save secret', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await wrapper.find('#secret-input-llm\\.api_key').setValue('sk-123')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    expect(wrapper.emitted('set')).toEqual([['sk-123']])
  })

  it('does not emit set for an empty draft', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    expect(wrapper.emitted('set')).toBeUndefined()
  })

  it('closes the input on Cancel without emitting set', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret() } })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await wrapper.find('#secret-input-llm\\.api_key').setValue('partial')
    await wrapper.find('[data-testid="secret-cancel-llm.api_key"]').trigger('click')
    expect(wrapper.emitted('set')).toBeUndefined()
    expect(wrapper.find('#secret-input-llm\\.api_key').exists()).toBe(false)
  })

  it('emits clear on Clear', async () => {
    const wrapper = mount(SettingSecret, { props: { setting: secret({ has_secret: true }) } })
    await wrapper.find('[data-testid="secret-clear-llm.api_key"]').trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
  })

  it('gives each action button a per-instance accessible name from the label', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true, label: 'TMDB API Key' }) },
    })
    expect(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').attributes('aria-label'),
    ).toBe('Replace TMDB API Key')
    expect(
      wrapper.find('[data-testid="secret-clear-llm.api_key"]').attributes('aria-label'),
    ).toBe('Clear TMDB API Key')

    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    expect(
      wrapper.find('[data-testid="secret-save-llm.api_key"]').attributes('aria-label'),
    ).toBe('Save TMDB API Key')
    expect(
      wrapper.find('[data-testid="secret-cancel-llm.api_key"]').attributes('aria-label'),
    ).toBe('Cancel replacing TMDB API Key')
  })

  it('names the action "Set" when no secret exists', () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: false, label: 'API Key' }) },
    })
    expect(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').attributes('aria-label'),
    ).toBe('Set API Key')
  })

  it('focuses the password input when Replace opens the edit row', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }) },
      attachTo: document.body,
    })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await nextTick()

    expect(document.activeElement).toBe(wrapper.find('#secret-input-llm\\.api_key').element)
    wrapper.unmount()
  })

  it('returns focus to the Replace button after Cancel', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }) },
      attachTo: document.body,
    })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="secret-cancel-llm.api_key"]').trigger('click')
    await nextTick()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').element,
    )
    wrapper.unmount()
  })

  it('returns focus to the Replace button after Save once busy clears', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }) },
      attachTo: document.body,
    })
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await nextTick()
    await wrapper.find('#secret-input-llm\\.api_key').setValue('sk-1')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    // The parent disables the button for the request; only once busy falls back
    // to false is the button focusable again.
    const startBusy: SecretProps = { busy: true }
    await wrapper.setProps(startBusy)
    await nextTick()
    const endBusy: SecretProps = { busy: false }
    await wrapper.setProps(endBusy)
    await nextTick()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').element,
    )
    wrapper.unmount()
  })

  it('returns focus to the Set button after Clear once busy clears', async () => {
    const wrapper = mount(SettingSecret, {
      props: { setting: secret({ has_secret: true }) },
      attachTo: document.body,
    })
    await wrapper.find('[data-testid="secret-clear-llm.api_key"]').trigger('click')
    const startBusy: SecretProps = { busy: true }
    await wrapper.setProps(startBusy)
    await nextTick()
    // Clearing removes the secret, so the button becomes "Set" (same control).
    const cleared: SecretProps = { busy: false, setting: secret({ has_secret: false }) }
    await wrapper.setProps(cleared)
    await nextTick()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').element,
    )
    wrapper.unmount()
  })
})
