import { readFileSync } from 'node:fs'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'
import { deferredFetch, jsonResponse } from '@/testing/http'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = { id: 1, username: 'aaron', display_name: 'Aaron Hall' }

function session(claimed: boolean, authenticated: boolean, user: UserResponse | null = null) {
  return jsonResponse(200, { claimed, authenticated, user })
}

/** The request the store made, as (url, init). */
function callTo(index: number): [string, RequestInit] {
  const [url, init] = vi.mocked(fetch).mock.calls[index]
  return [String(url), init ?? {}]
}

// fetch is stubbed rather than useApi mocked: the store calls fetch itself, so
// that it never imports the API layer that imports it.
describe('useAuthStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders none of the three screens until the boot call answers', () => {
    deferredFetch()
    const auth = useAuthStore()

    auth.resolveSession()

    expect(auth.state).toBe('unknown')
    expect(auth.needsSetup).toBe(false)
    expect(auth.needsLogin).toBe(false)
    expect(auth.isAuthenticated).toBe(false)
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

  it('holds the account the session names, for the sidebar to label', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()

    await auth.resolveSession()

    expect(auth.user).toEqual(AARON)
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

  it('falls back to its own wording when the refusal is a validation shape', async () => {
    // Rendering a 422 detail straight would put "[object Object]" on the screen:
    // it is a list of field errors rather than a sentence.
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(422, { detail: [{ loc: ['body', 'password'], msg: 'too short' }] }),
    )
    const auth = useAuthStore()

    expect(await auth.signIn({ username: 'aaron', password: 'x' })).toMatch(/not accepted/)
  })

  it('creates the first account, and lands signed in without a second call', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, AARON))
    const auth = useAuthStore()

    expect(
      await auth.signUp({ username: 'aaron', display_name: 'Aaron Hall', password: 'hunter22' }),
    ).toBe('')

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(callTo(0)[0]).toBe('/api/auth/setup')
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.user).toEqual(AARON)
  })

  it('reports a claimed instance rather than pretending setup worked', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(409, { detail: 'This instance already has an account. Sign in instead.' }),
    )
    const auth = useAuthStore()

    expect(await auth.signUp({ username: 'aaron', display_name: '', password: 'hunter22' })).toBe(
      'This instance already has an account. Sign in instead.',
    )
    expect(auth.isAuthenticated).toBe(false)
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

  it('signs out even when the logout request never lands', async () => {
    // Leaving the shell up would tell the user their tap did nothing, and a
    // server that cannot be reached is not holding the session open either.
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    await auth.signOut()

    expect(auth.needsLogin).toBe(true)
  })

  it('drops the session when a request comes back refused', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    auth.reject()

    expect(auth.isAuthenticated).toBe(false)
    expect(auth.needsLogin).toBe(true)
    expect(auth.user).toBeNull()
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

  it('sends the user back to sign-in when the rename finds no session', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'Not signed in.' }))

    expect(await auth.updateProfile({ username: 'bob', display_name: '' })).toMatch(/Sign in again/)
    expect(auth.needsLogin).toBe(true)
  })

  it('changes the password against the account route', async () => {
    vi.mocked(fetch).mockResolvedValue(session(true, true, AARON))
    const auth = useAuthStore()
    await auth.resolveSession()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
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

  it('never imports the API layer, which imports this store', () => {
    // useApi calls useAuthStore(), so importing it back is the cycle
    // DEVELOPMENT_PATTERNS bans. Asserted on the source because a cycle the
    // bundler has already resolved breaks at boot, not under test.
    const source = readFileSync(`${process.cwd()}/resources/js/stores/auth.ts`, 'utf8')

    // Unanchored: a relative '../composables/useApi' closes the same cycle.
    expect(source).not.toMatch(/composables\/useApi/)
  })
})
