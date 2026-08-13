import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SettingsPage from './SettingsPage.vue'
import { useAuthStore } from '@/stores/auth'
import { jsonResponse } from '@/testing/http'
import { formatDate } from '@/utils/format'
import type { UserResponse } from '@/types/api'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

// The settings store is stubbed here. The auth store's own routes are not: the
// account tests below assert on the request that reached the network.
vi.mock('@/composables/useApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/useApi')>()
  const authRoute = /^\/(auth|users)\b/

  return {
    ...actual,
    useApi: () => {
      const real = actual.useApi()
      return {
        ...real,
        get: (path: string, params?: Record<string, string | number | boolean | undefined>) =>
          authRoute.test(path) ? real.get(path, params) : mockGet(path, params),
        put: (path: string, body?: unknown, options?: { sessionSurvives401?: boolean }) =>
          authRoute.test(path) ? real.put(path, body, options) : mockPut(path, body),
        delete: (path: string, params?: Record<string, string | number | boolean | undefined>) =>
          authRoute.test(path) ? real.delete(path, params) : mockDelete(path, params),
      }
    },
  }
})

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

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

// Asserted on the stubbed global fetch: the mock above lets the account routes
// through to the real API layer, so what these check is the request itself.
describe('SettingsPage account section', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockGet.mockResolvedValue({ sections: [] })
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function signedIn() {
    const auth = useAuthStore()
    auth.$patch({ state: 'signed-in', user: AARON })
    return auth
  }

  async function openSettings(): Promise<VueWrapper> {
    const wrapper = mount(SettingsPage)
    await flushPromises()
    return wrapper
  }

  // Long enough to clear the rule the form checks before it submits anything.
  const REPLACEMENT = 'hunter33-hunter33'

  async function changePassword(wrapper: VueWrapper): Promise<void> {
    await wrapper.find('#account-current-password').setValue('hunter2')
    await wrapper.find('#account-new-password').setValue(REPLACEMENT)
    await wrapper.find('#account-confirm-password').setValue(REPLACEMENT)
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
  }

  it('waits for the session before offering an account to edit', async () => {
    const wrapper = await openSettings()

    expect(wrapper.find('#account-heading').exists()).toBe(false)
  })

  it('shows the account facts only the session carries', async () => {
    // The floor and the password's age both arrive on the session call, and
    // both used to stop at the store: one was hardcoded, one was not shown.
    const auth = signedIn()
    auth.$patch({ minPasswordLength: 16 })
    const wrapper = await openSettings()

    expect(wrapper.find('#account-new-password-hint').text()).toContain('16')
    expect(wrapper.find('[data-testid="account-password-age"]').text()).toContain(
      formatDate(AARON.password_updated_at as string),
    )
  })

  it('renames the account, and shows the new name as saved', async () => {
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, { id: 1, username: 'aaron', display_name: 'Aaron' }),
    )
    const wrapper = await openSettings()

    await wrapper.find('#account-display-name').setValue('Aaron')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toBe('/api/users/1')
    expect(init?.method).toBe('PATCH')
    expect(init?.body).toBe(JSON.stringify({ username: 'aaron', display_name: 'Aaron' }))
    expect(auth.user?.display_name).toBe('Aaron')
    expect(wrapper.find('#account-profile-status').text()).toBe('Saved.')
  })

  it('changes the username, which is what the next sign-in needs', async () => {
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, { id: 1, username: 'renamed', display_name: 'Aaron Hall' }),
    )
    const wrapper = await openSettings()

    await wrapper.find('#account-username').setValue('renamed')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toBe('/api/users/1')
    expect(init?.body).toBe(
      JSON.stringify({ username: 'renamed', display_name: 'Aaron Hall' }),
    )
    expect(auth.user?.username).toBe('renamed')
    expect(wrapper.find<HTMLInputElement>('#account-username').element.value).toBe('renamed')
  })

  it('reports a refused rename beside the form that was refused', async () => {
    signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(409, { detail: 'That username is taken.' }))
    const wrapper = await openSettings()

    await wrapper.find('#account-username').setValue('bob')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()

    expect(wrapper.find('#account-profile-status').text()).toBe('That username is taken.')
    expect(wrapper.find('#account-password-status').text()).toBe('')
  })

  it('changes the password against the account route', async () => {
    signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
    const wrapper = await openSettings()

    await changePassword(wrapper)

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toBe('/api/users/1/password')
    expect(init?.method).toBe('PUT')
    expect(wrapper.find('#account-password-status').text()).toBe('Password changed.')
  })

  it('keeps the user on the page when the current password is wrong', async () => {
    // A typo that emptied the whole screen would be a hard thing to recover
    // from, and this route answers 401 for the field as well as the session.
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { detail: 'That is not your current password.' }),
    )
    const wrapper = await openSettings()

    await changePassword(wrapper)

    expect(wrapper.find('#account-password-status').text()).toBe(
      'That is not your current password.',
    )
    expect(auth.isAuthenticated).toBe(true)
  })

  it('signs out through the account section', async () => {
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
    const wrapper = await openSettings()

    await wrapper.find('[data-testid="account-sign-out"]').trigger('click')
    await flushPromises()

    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe('/api/auth/logout')
    expect(auth.needsLogin).toBe(true)
  })
})
