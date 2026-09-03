import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SettingsPage from './SettingsPage.vue'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'
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
        has_stored_value: false,
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

  /** The change answers 204; the session behind it carries *changedAt*, which
   *  is the only place the new date comes from. */
  function passwordChanged(changedAt: string) {
    return (url: RequestInfo | URL) =>
      Promise.resolve(
        String(url).endsWith('/password')
          ? jsonResponse(204)
          : jsonResponse(200, {
              claimed: true,
              authenticated: true,
              user: { ...AARON, password_updated_at: changedAt },
              min_password_length: PASSWORD_MIN_LENGTH,
            }),
      )
  }

  async function changePassword(wrapper: VueWrapper): Promise<void> {
    await wrapper.find('#account-current-password').setValue('hunter2')
    await wrapper.find('#account-new-password').setValue(REPLACEMENT)
    await wrapper.find('#account-confirm-password').setValue(REPLACEMENT)
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()
  }

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

  it('dates the password from the change, not from the session it opened on', async () => {
    // Regression: the 204 carries no body and nothing re-read the session, so
    // the line above the form still said 15 January while the region below it
    // announced the change — and nothing on screen said which was true.
    const CHANGED_AT = '2026-02-01T10:00:00+00:00'
    signedIn()
    vi.mocked(fetch).mockImplementation(passwordChanged(CHANGED_AT))
    const wrapper = await openSettings()
    const age = () => wrapper.find('[data-testid="account-password-age"]').text()
    expect(age()).toContain(formatDate(AARON.password_updated_at as string))

    await changePassword(wrapper)

    expect(age()).toContain(formatDate(CHANGED_AT))
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
