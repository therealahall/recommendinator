import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { useApi, ApiError } from './useApi'
import { jsonResponse } from '@/testing/http'

describe('useApi ApiError', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('attaches the parsed JSON body of an error response to ApiError.body', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(422, { detail: { key: 'web.port', reason: 'out of range' } }),
    )

    const api = useApi()
    await expect(api.put('/settings', { updates: {} })).rejects.toMatchObject({
      status: 422,
      body: { detail: { key: 'web.port', reason: 'out of range' } },
    })
  })

  it('keeps the status line as the message when detail is a validation object', async () => {
    // A 422 payload is a shape, not a sentence: settings reads `body` for the
    // field wording, and anything showing `message` needs something readable.
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve({ detail: { key: 'web.port', reason: 'out of range' } }),
      text: () => Promise.resolve(''),
    } as unknown as Response)

    const err = await useApi()
      .put('/settings', { updates: {} })
      .catch((e: unknown) => e)

    expect((err as ApiError).message).toBe('422 Unprocessable Entity')
  })

  it('takes the message from a string detail, which is written for the user', async () => {
    // Regression: the stream cap's "try again in a moment" reached no screen,
    // because every message came from the status line instead of the body.
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(503, { detail: 'Too many streams in progress. Try again in a moment.' }),
    )

    const err = await useApi().get('/recommendations').catch((e: unknown) => e)

    expect((err as ApiError).message).toBe('Too many streams in progress. Try again in a moment.')
  })

  it.each([
    ['empty', ''],
    ['whitespace', '   '],
  ])('keeps the status line as the message when the detail is %s', async (_label, detail) => {
    // A blank detail is still a string, so it beat the status line and the
    // page rendered "Failed to load recommendations: " and nothing after it.
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve({ detail }),
      text: () => Promise.resolve(''),
    } as unknown as Response)

    const err = await useApi().get('/recommendations').catch((e: unknown) => e)

    expect((err as ApiError).message).toBe('503 Service Unavailable')
  })

  it('leaves body undefined when the error response has no JSON', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      headers: { get: () => 'text/plain' },
      json: () => Promise.reject(new Error('no json')),
      text: () => Promise.resolve('boom'),
    } as unknown as Response)

    const api = useApi()
    const err = await api.get('/settings').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).body).toBeUndefined()
    expect((err as ApiError).message).toBe('500 Internal Server Error')
  })
})

describe('useApi query parameters', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function requestedUrl(call = 0): string {
    return String(vi.mocked(fetch).mock.calls[call][0])
  }

  it('carries params on the URL of a POST that already has a body', async () => {
    // The OAuth writes key on ``source_id`` here and nowhere else: a store test
    // asserting the mock's arguments passes just as well with the query string
    // dropped, and every token then lands on the wrong source.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { message: 'ok' }))

    await useApi().post('/gog/exchange', { code_or_url: 'abc' }, { source_id: 'gog_work' })

    expect(requestedUrl()).toBe('/api/gog/exchange?source_id=gog_work')
    expect(vi.mocked(fetch).mock.calls[0][1]?.body).toBe(
      JSON.stringify({ code_or_url: 'abc' }),
    )
  })

  it('carries them on a DELETE, which has no body to put them in', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { message: 'ok' }))

    await useApi().delete('/gog/token', { source_id: 'gog_work' })

    expect(requestedUrl()).toBe('/api/gog/token?source_id=gog_work')
  })

  it('percent-encodes a param value rather than splicing it into the URL', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}))

    await useApi().get('/trakt/status', { source_id: 'trakt&admin=1' })

    expect(requestedUrl()).toBe('/api/trakt/status?source_id=trakt%26admin%3D1')
  })
})

function initOf(call: number): RequestInit {
  return vi.mocked(fetch).mock.calls[call][1] ?? {}
}

/** Put the store where a live session leaves it, without a boot round trip. */
function signedIn() {
  const auth = useAuthStore()
  auth.$patch({
    state: 'signed-in',
    user: { id: 1, username: 'aaron', display_name: null, password_updated_at: null },
  })
  return auth
}

describe('useApi authentication', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the session cookie, which is the only credential the SPA has', async () => {
    signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().get('/users')

    expect(initOf(0).credentials).toBe('include')
  })

  it('keeps the caller-supplied headers alongside it', async () => {
    signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}))

    await useApi().post('/users', {})

    expect(initOf(0).headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(initOf(0).credentials).toBe('include')
  })

  it('ends the session the server refuses, so the sign-in screen comes back', async () => {
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'Not signed in.' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.isAuthenticated).toBe(false)
    expect(auth.needsLogin).toBe(true)
  })

  it('leaves it alone where the route answers 401 for the request itself', async () => {
    // Changing a password is refused with a 401 when the current one is wrong,
    // and signing the user out there would cost one typo the whole screen.
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { detail: 'That is not your current password.' }),
    )

    await expect(
      useApi().put('/users/1/password', { current_password: 'wrong' }, { sessionSurvives401: true }),
    ).rejects.toBeInstanceOf(ApiError)

    expect(auth.isAuthenticated).toBe(true)
  })

  it('leaves the session alone on any other failure', async () => {
    const auth = signedIn()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(503, { detail: 'down' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.isAuthenticated).toBe(true)
  })
})
