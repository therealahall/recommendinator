import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'
import { jsonResponse } from '@/testing/http'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

function session(
  claimed: boolean,
  authenticated: boolean,
  user: UserResponse | null = null,
  minPasswordLength: number = PASSWORD_MIN_LENGTH,
) {
  return jsonResponse(200, {
    claimed,
    authenticated,
    user,
    min_password_length: minPasswordLength,
  })
}

/** The request the store made, as (url, init). */
function callTo(index: number): [string, RequestInit] {
  const [url, init] = vi.mocked(fetch).mock.calls[index]
  return [String(url), init ?? {}]
}

// fetch is stubbed rather than useApi mocked: the store goes through the API
// layer like everything else, and these assertions are on what left the browser.
describe('useAuthStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const SCREENS: Array<{ instance: string; answer: Response; state: string }> = [
    { instance: 'a fresh one', answer: session(false, false), state: 'unclaimed' },
    { instance: 'a claimed one', answer: session(true, false), state: 'signed-out' },
    { instance: 'a signed-in one', answer: session(true, true, AARON), state: 'signed-in' },
  ]

  it.each(SCREENS)('resolves $instance from one session call', async ({ answer, state }) => {
    vi.mocked(fetch).mockResolvedValue(answer)
    const auth = useAuthStore()

    expect(await auth.resolveSession()).toBe('')

    expect(auth.state).toBe(state)
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = callTo(0)
    expect(url).toBe('/api/auth/session')
    expect(init.credentials).toBe('include')
    // Reading the session must not be the thing that changes it.
    expect(init.method ?? 'GET').toBe('GET')
  })

  it('takes the password floor from the session, not from a literal of its own', async () => {
    // Regression: the SPA hardcoded 12, so a server enforcing anything else
    // stated a rule it does not have and refused passwords it accepts.
    const server = PASSWORD_MIN_LENGTH + 4
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON, server))
    const auth = useAuthStore()

    await auth.resolveSession()

    expect(auth.minPasswordLength).toBe(server)
  })

  it('opens on sign-in, saying so, when the server does not answer at all', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const auth = useAuthStore()

    expect(await auth.resolveSession()).toMatch(/did not answer/)

    expect(auth.needsLogin).toBe(true)
    expect(auth.user).toBeNull()
  })

  it('signs in, and adopts the account the server hands back', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, AARON))
    const auth = useAuthStore()

    expect(await auth.signIn({ username: 'aaron', password: 'hunter2' })).toBe('')

    const [url, init] = callTo(0)
    expect(url).toBe('/api/auth/login')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
    expect(init.body).toBe(JSON.stringify({ username: 'aaron', password: 'hunter2' }))
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.user).toEqual(AARON)
  })

  it("repeats the server's refusal, which is the wording written for the user", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { detail: 'That username and password do not match an account.' }),
    )
    const auth = useAuthStore()

    expect(await auth.signIn({ username: 'aaron', password: 'wrong' })).toBe(
      'That username and password do not match an account.',
    )
    expect(auth.isAuthenticated).toBe(false)
  })

  it('reports a claimed instance, and moves to the form that advice needs', async () => {
    // Regression: a second tab claiming first left the setup screen up, so
    // "sign in instead" pointed at a form that was not on the page.
    vi.mocked(fetch).mockImplementation((url) =>
      Promise.resolve(
        String(url).endsWith('/setup')
          ? jsonResponse(409, { detail: 'This instance already has an account. Sign in instead.' })
          : session(true, false),
      ),
    )
    const auth = useAuthStore()

    expect(await auth.signUp({ username: 'aaron', display_name: '', password: 'hunter22' })).toBe(
      'This instance already has an account. Sign in instead.',
    )
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.needsLogin).toBe(true)
    expect(auth.needsSetup).toBe(false)
  })

  it('signs out, and keeps nothing a reload could restore the session from', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
    await auth.signOut()

    const [url, init] = callTo(1)
    expect(url).toBe('/api/auth/logout')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
    expect(auth.needsLogin).toBe(true)
    expect(auth.user).toBeNull()
    // The cookie is the whole session, and the server has just expired it.
    expect(localStorage.length).toBe(0)
  })

  it('renames the account, and republishes the name the sidebar reads', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(200, { id: 1, username: 'aaron', display_name: 'Aaron' }),
    )
    expect(await auth.updateProfile({ username: 'aaron', display_name: 'Aaron' })).toBe('')

    const [url, init] = callTo(1)
    expect(url).toBe('/api/users/1')
    expect(init.method).toBe('PATCH')
    expect(init.credentials).toBe('include')
    expect(auth.user?.display_name).toBe('Aaron')
  })

  /** The change answers 204; every other call is the session behind it. */
  function passwordChanged(user: UserResponse) {
    return (url: RequestInfo | URL) =>
      Promise.resolve(
        String(url).endsWith('/password') ? jsonResponse(204) : session(true, true, user),
      )
  }

  it('changes the password against the account route', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockImplementation(passwordChanged(AARON))
    expect(
      await auth.changePassword({ current_password: 'hunter2', new_password: 'hunter33' }),
    ).toBe('')

    const [url, init] = callTo(1)
    expect(url).toBe('/api/users/1/password')
    expect(init.method).toBe('PUT')
    expect(init.body).toBe(
      JSON.stringify({ current_password: 'hunter2', new_password: 'hunter33' }),
    )
  })

  it('re-reads the session after the change, which answers with no new date', async () => {
    // Regression: the 204 left the account showing the old "Password changed"
    // date beside a live region announcing the change, and only a reload agreed
    // with either.
    const changed = { ...AARON, password_updated_at: '2026-02-01T10:00:00+00:00' }
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockImplementation(passwordChanged(changed))
    await auth.changePassword({ current_password: 'hunter2', new_password: 'hunter33' })

    expect(callTo(2)[0]).toBe('/api/auth/session')
    expect(auth.user).toEqual(changed)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('keeps the session when the current password is wrong', async () => {
    // One typo would otherwise empty the whole screen: this route answers 401
    // for the field as well as for a dead session.
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { detail: 'That is not your current password.' }),
    )

    expect(await auth.changePassword({ current_password: 'wrong', new_password: 'hunter33' })).toBe(
      'That is not your current password.',
    )
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.user).toEqual(AARON)
  })
})
