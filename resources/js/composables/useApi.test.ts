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

  it('percent-encodes a param value rather than splicing it into the URL', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}))

    await useApi().get('/trakt/status', { source_id: 'trakt&admin=1' })

    expect(requestedUrl()).toBe('/api/trakt/status?source_id=trakt%26admin%3D1')
  })
})

describe('useApi upload', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('leaves the Content-Type of a multipart body to the browser', async () => {
    // Naming it here drops the boundary only the browser knows, and the server
    // then finds no part at all in a body that looks well formed.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { added: 1 }))
    const form = new FormData()
    form.append('importer', 'goodreads_csv')

    await useApi().upload('/import', form)

    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(init?.headers).toEqual({})
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(form)
  })
})

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
})
