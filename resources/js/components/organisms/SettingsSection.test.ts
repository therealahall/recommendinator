import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SettingsSection from './SettingsSection.vue'
import type { SettingsSection as SettingsSectionType, SettingView } from '@/types/api'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

const { MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    constructor(
      public status: number,
      public statusText: string,
      public body?: unknown,
    ) {
      super(`${status} ${statusText}`)
      this.name = 'ApiError'
    }
  }
  return { MockApiError }
})

vi.mock('@/composables/useApi', () => ({
  ApiError: MockApiError,
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
    raw: vi.fn(),
  }),
}))

function textSetting(key: string, value: string, extra: Partial<SettingView> = {}): SettingView {
  return {
    key,
    section: 'web',
    label: key,
    help: '',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: false,
    value,
    db_overridden: false,
    ...extra,
  } as SettingView
}

function secretSetting(key: string, extra: Partial<SettingView> = {}): SettingView {
  return {
    key,
    section: 'llm',
    label: 'API Key',
    help: '',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: true,
    has_secret: false,
    ...extra,
  } as SettingView
}

function mountSection(section: SettingsSectionType) {
  return mount(SettingsSection, { props: { section }, attachTo: document.body })
}

describe('SettingsSection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  it('renders one control per non-advanced value setting under a humanized heading', () => {
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost'), textSetting('web.title', 'x')],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('h3').text()).toBe('Web')
    expect(wrapper.find('[data-testid="setting-web.host"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="setting-web.title"]').exists()).toBe(true)
  })

  it('saves only the changed keys and labels the Save button by section', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost'), textSetting('web.title', 'x')],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('[data-testid="save-web"]').text()).toBe('Save Web')
    await wrapper.find('[data-testid="setting-web.host"]').setValue('0.0.0.0')
    await wrapper.find('[data-testid="save-web"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'web.host': '0.0.0.0' } })
  })

  it('maps a 422 to the offending field and moves focus to it', async () => {
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'web.host', reason: 'invalid host' },
      }),
    )
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost')],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="setting-web.host"]').setValue('!!')
    await wrapper.find('[data-testid="save-web"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="setting-error-web.host"]').text()).toBe('invalid host')
    expect(document.activeElement?.id).toBe('setting-web.host')
    wrapper.unmount()
  })

  it('resets an overridden setting via DELETE', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-web.host"]').trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith('/settings/web.host')
  })

  it('renders restart and overridden pills for the relevant settings', () => {
    const section: SettingsSectionType = {
      section: 'web',
      settings: [
        textSetting('web.host', 'localhost', { restart_required: true, db_overridden: true }),
      ],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('[data-testid="restart-badge-web.host"]').text()).toContain('Requires restart')
    expect(wrapper.find('[data-testid="overridden-badge-web.host"]').text()).toContain('Overridden')
  })

  it('renders secrets in a Secrets fieldset and saves them out of band', async () => {
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'llm',
      settings: [
        {
          key: 'llm.api_key',
          section: 'llm',
          label: 'API Key',
          help: '',
          type: 'string',
          widget: 'text',
          choices: null,
          validation: null,
          advanced: false,
          restart_required: false,
          sensitive: true,
          has_secret: false,
        } as SettingView,
      ],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('.source-form-secrets legend').text()).toBe('Secrets')
    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await wrapper.find('#secret-input-llm\\.api_key').setValue('sk-999')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', { key: 'llm.api_key', value: 'sk-999' })
  })

  it('collapses advanced settings into a keyboard-operable disclosure with a caution note', async () => {
    const section: SettingsSectionType = {
      section: 'web',
      settings: [
        textSetting('web.host', 'localhost'),
        textSetting('web.cors', 'x', { advanced: true }),
      ],
    }
    const wrapper = mountSection(section)

    const trigger = wrapper.find('.accordion-trigger')
    expect(trigger.text()).toContain('Advanced · 1 setting')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // Panel is hidden while collapsed.
    expect(wrapper.find('.accordion-panel').attributes('hidden')).toBeDefined()
    expect(wrapper.find('[role="note"]').exists()).toBe(true)

    // Native button trigger toggles on activation (keyboard Enter/Space).
    await trigger.trigger('click')
    expect(wrapper.find('.accordion-trigger').attributes('aria-expanded')).toBe('true')
  })

  it('nests the Advanced disclosure heading at h4 under the section h3', () => {
    const section: SettingsSectionType = {
      section: 'web',
      settings: [
        textSetting('web.host', 'localhost'),
        textSetting('web.cors', 'x', { advanced: true }),
      ],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('h4.accordion-heading .accordion-trigger').exists()).toBe(true)
    expect(wrapper.find('h3.accordion-heading').exists()).toBe(false)
  })

  it('marks the control as resetting while the DELETE is in flight', async () => {
    let resolveDelete: (v: unknown) => void = () => {}
    mockDelete.mockReturnValue(
      new Promise((resolve) => {
        resolveDelete = resolve
      }),
    )
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-web.host"]').trigger('click')
    await nextTick()
    const btn = wrapper.find('[data-testid="reset-web.host"]')
    expect(btn.text()).toBe('Resetting…')
    expect(btn.attributes('disabled')).toBeDefined()

    resolveDelete({ sections: [] })
    await flushPromises()
    wrapper.unmount()
  })

  it('announces the reset and returns focus to the control', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-web.host"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('p.sr-only').text()).toBe('Reset to default.')
    expect(document.activeElement?.id).toBe('setting-web.host')
    wrapper.unmount()
  })

  it('re-announces an identical repeated action by re-mutating the live region', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'web',
      settings: [textSetting('web.host', 'localhost', { db_overridden: true })],
    }
    const wrapper = mountSection(section)
    const region = wrapper.find('p.sr-only').element

    await wrapper.find('[data-testid="reset-web.host"]').trigger('click')
    await flushPromises()
    expect(region.textContent).toBe('Reset to default.')

    // Reassigning the same string is a no-op for a persistent status region, so
    // AT stays silent. The fix blanks the region for one tick before re-setting
    // it, so a repeated identical reset passes through '' — a real mutation the
    // screen reader re-announces. Pump microtasks until the message re-settles,
    // capturing that transient blank without depending on a fixed tick count
    // (which could miss it if the mocked promise resolves at a different depth).
    const seen = new Set<string>()
    void wrapper.find('[data-testid="reset-web.host"]').trigger('click')
    for (let i = 0; i < 25; i++) {
      await nextTick()
      seen.add(region.textContent ?? '')
      if (seen.has('') && region.textContent === 'Reset to default.') break
    }
    await flushPromises()

    expect([...seen]).toContain('')
    expect(region.textContent).toBe('Reset to default.')
    wrapper.unmount()
  })

  it('disables the Replace button and announces success while a secret set is in flight', async () => {
    let resolvePut: (v: unknown) => void = () => {}
    mockPut.mockReturnValue(
      new Promise((resolve) => {
        resolvePut = resolve
      }),
    )
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'llm',
      settings: [secretSetting('llm.api_key')],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await wrapper.find('#secret-input-llm\\.api_key').setValue('sk-1')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    await nextTick()

    expect(
      wrapper.find('[data-testid="secret-replace-llm.api_key"]').attributes('disabled'),
    ).toBeDefined()

    resolvePut(undefined)
    await flushPromises()
    expect(wrapper.find('p.sr-only').text()).toBe('Secret saved.')
    wrapper.unmount()
  })

  it('announces "Secret cleared." after clearing a secret', async () => {
    mockDelete.mockResolvedValue(undefined)
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'llm',
      settings: [secretSetting('llm.api_key', { has_secret: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="secret-clear-llm.api_key"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('p.sr-only').text()).toBe('Secret cleared.')
    wrapper.unmount()
  })

  it('auto-clears the saved status after the timeout elapses', async () => {
    vi.useFakeTimers()
    try {
      mockPut.mockResolvedValue({ sections: [] })
      const section: SettingsSectionType = {
        section: 'web',
        settings: [textSetting('web.host', 'localhost')],
      }
      const wrapper = mountSection(section)

      await wrapper.find('[data-testid="setting-web.host"]').setValue('0.0.0.0')
      await wrapper.find('[data-testid="save-web"]').trigger('click')
      await vi.advanceTimersByTimeAsync(0)
      await nextTick()
      expect(wrapper.find('[data-testid="save-status-web"]').text()).toBe('Saved ✓')

      await vi.advanceTimersByTimeAsync(2500)
      await nextTick()
      expect(wrapper.find('[data-testid="save-status-web"]').exists()).toBe(false)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
