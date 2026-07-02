import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SettingsPage from './SettingsPage.vue'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/composables/useApi', () => ({
  ApiError: class ApiError extends Error {},
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
    raw: vi.fn(),
  }),
}))

function secretSection(hasSecret: boolean) {
  return {
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
        has_secret: hasSecret,
      },
    ],
  }
}

function section(name: string) {
  return {
    section: name,
    settings: [
      {
        key: `${name}.host`,
        section: name,
        label: 'Host',
        help: '',
        type: 'string',
        widget: 'text',
        choices: null,
        validation: null,
        advanced: false,
        restart_required: false,
        sensitive: false,
        value: 'x',
        db_overridden: false,
      },
    ],
  }
}

describe('SettingsPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  it('renders a card with a humanized heading per section', async () => {
    mockGet.mockResolvedValue({ sections: [section('web'), section('llm')] })
    const wrapper = mount(SettingsPage)
    await flushPromises()

    const headings = wrapper.findAll('h3').map((h) => h.text())
    expect(headings).toContain('Web')
    expect(headings).toContain('LLM')
  })

  it('shows a loading state before settings arrive', async () => {
    mockGet.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(SettingsPage)
    await flushPromises()
    expect(wrapper.text()).toContain('Loading settings')
    expect(wrapper.find('[aria-busy="true"]').exists()).toBe(true)
  })

  it('shows an empty state when there are no settings', async () => {
    mockGet.mockResolvedValue({ sections: [] })
    const wrapper = mount(SettingsPage)
    await flushPromises()
    expect(wrapper.text()).toContain('No configurable settings')
  })

  it('shows an error state with a Retry button when the load fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'))
    const wrapper = mount(SettingsPage)
    await flushPromises()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.text()).toContain("Couldn't load settings")
    expect(wrapper.find('[data-testid="settings-retry"]').exists()).toBe(true)

    mockGet.mockResolvedValue({ sections: [section('web')] })
    await wrapper.find('[data-testid="settings-retry"]').trigger('click')
    await flushPromises()
    expect(mockGet).toHaveBeenCalledTimes(2)
  })

  // A secret action must refresh the section tree in place, not through the
  // global loading flag. Toggling loading would remount every section, dropping
  // focus to <body> and defeating SettingSecret's focus restoration (WCAG 2.4.3).
  it('keeps focus on the secret control through a full Save cycle', async () => {
    mockGet.mockResolvedValueOnce({ sections: [secretSection(false)] })
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValueOnce({ sections: [secretSection(true)] })
    const wrapper = mount(SettingsPage, { attachTo: document.body })
    await flushPromises()

    await wrapper.find('[data-testid="secret-replace-llm.api_key"]').trigger('click')
    await nextTick()
    await wrapper.find('#secret-input-llm\\.api_key').setValue('sk-123')
    await wrapper.find('[data-testid="secret-save-llm.api_key"]').trigger('click')
    await flushPromises()
    await nextTick()

    const replace = wrapper.find('[data-testid="secret-replace-llm.api_key"]')
    expect(replace.exists()).toBe(true)
    expect(document.activeElement).toBe(replace.element)
    wrapper.unmount()
  })

  it('keeps focus on the secret control through a full Clear cycle', async () => {
    mockGet.mockResolvedValueOnce({ sections: [secretSection(true)] })
    mockDelete.mockResolvedValue(undefined)
    mockGet.mockResolvedValueOnce({ sections: [secretSection(false)] })
    const wrapper = mount(SettingsPage, { attachTo: document.body })
    await flushPromises()

    await wrapper.find('[data-testid="secret-clear-llm.api_key"]').trigger('click')
    await flushPromises()
    await nextTick()

    const setButton = wrapper.find('[data-testid="secret-replace-llm.api_key"]')
    expect(setButton.exists()).toBe(true)
    expect(document.activeElement).toBe(setButton.element)
    wrapper.unmount()
  })
})
