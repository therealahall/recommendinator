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

  // Regression: aria-busy="true" sat on the settings card, which only renders
  // while the page has no sections to show. On the common outcome — settings
  // arrive — that node is replaced by the section list, so assistive tech
  // tracking the busy state saw it vanish and never heard that loading finished
  // (4.1.3). Every outcome is enumerated because the two that re-render the
  // card in place passed even with the flag in the wrong place.
  const LOAD_OUTCOMES: Array<{
    outcome: string
    settle: (resolve: (value: unknown) => void, reject: (error: unknown) => void) => void
    shows: string
  }> = [
    {
      outcome: 'settings arrive',
      settle: (resolve) => resolve({ sections: [section('web')] }),
      shows: 'Web',
    },
    {
      outcome: 'there are no settings',
      settle: (resolve) => resolve({ sections: [] }),
      shows: 'No configurable settings',
    },
    {
      outcome: 'the load fails',
      settle: (_resolve, reject) => reject(new Error('boom')),
      shows: "Couldn't load settings",
    },
  ]

  it.each(LOAD_OUTCOMES)('clears aria-busy in place when $outcome', async ({ settle, shows }) => {
    let resolveGet: (value: unknown) => void = () => {}
    let rejectGet: (error: unknown) => void = () => {}
    mockGet.mockReturnValue(
      new Promise((resolve, reject) => {
        resolveGet = resolve
        rejectGet = reject
      }),
    )
    const wrapper = mount(SettingsPage)
    await flushPromises()

    const busy = wrapper.find('[aria-busy="true"]')
    expect(busy.exists()).toBe(true)
    // The flag has to live on the page wrapper: it is the only node present in
    // every outcome, so it is the only one that can flip rather than unmount.
    expect(busy.element).toBe(wrapper.element)

    settle(resolveGet, rejectGet)
    await flushPromises()

    expect(busy.attributes('aria-busy')).toBeUndefined()
    expect(wrapper.text()).toContain(shows)
  })

  it('shows an empty state when there are no settings', async () => {
    mockGet.mockResolvedValue({ sections: [] })
    const wrapper = mount(SettingsPage)
    await flushPromises()
    expect(wrapper.text()).toContain('No configurable settings')
  })

  it('keeps the Retry button out of the alert region', async () => {
    // Alert content is announced as one chunk, so a button inside it has its
    // affordance buried in the error prose.
    mockGet.mockRejectedValue(new Error('boom'))
    const wrapper = mount(SettingsPage)
    await flushPromises()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.find('[data-testid="settings-retry"]').exists()).toBe(false)
    // Defaults to type="submit" without this, which would post a wrapping form.
    expect(wrapper.find('[data-testid="settings-retry"]').attributes('type')).toBe(
      'button',
    )
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
