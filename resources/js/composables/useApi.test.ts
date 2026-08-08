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

function headersOf(call: number): Record<string, string> {
  return vi.mocked(fetch).mock.calls[call][1]?.headers as Record<string, string>
}

/** Seed storage rather than calling an action: the store only persists a token
 *  it has verified, and these tests are about what happens afterwards. */
function storedToken(value: string) {
  localStorage.setItem('apiToken', value)
  return useAuthStore()
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

  it('sends the stored token as a bearer credential', async () => {
    storedToken('the-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().get('/users')

    expect(headersOf(0)['Authorization']).toBe('Bearer the-token')
  })

  it('sends it on streaming requests too, which bypass request()', async () => {
    storedToken('the-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().raw('/recommendations/stream', { method: 'GET' })

    expect(headersOf(0)['Authorization']).toBe('Bearer the-token')
  })

  it('keeps the caller-supplied headers alongside it', async () => {
    storedToken('the-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}))

    await useApi().raw('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })

    expect(headersOf(0)).toMatchObject({
      Authorization: 'Bearer the-token',
      'Content-Type': 'application/json',
    })
  })

  it('sends no Authorization header when there is no token', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().get('/users')

    expect(headersOf(0)['Authorization']).toBeUndefined()
  })

  it('drops a token the server refuses, so the gate asks again', async () => {
    const auth = storedToken('stale-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'nope' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.isAuthenticated).toBe(false)
    expect(auth.status).toBe('rejected')
  })

  it('drops it on a refused stream too, which returns instead of throwing', async () => {
    // Regression: raw() carried the bearer header but never inspected the
    // status, so a token revoked mid-session surfaced to the SSE stores as a
    // bare "HTTP 401" and left the user with no way back to the gate.
    const auth = storedToken('revoked-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'nope' }))

    const response = await useApi().raw('/recommendations/stream')

    expect(response.status).toBe(401)
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.status).toBe('rejected')
  })

  it('leaves the token alone on any other failure', async () => {
    const auth = storedToken('good-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(503, { detail: 'down' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.token).toBe('good-token')
    expect(auth.status).toBe('idle')
  })

  it('leaves a streaming failure that is not a refusal alone', async () => {
    const auth = storedToken('good-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(503, { detail: 'down' }))

    await useApi().raw('/recommendations/stream')

    expect(auth.token).toBe('good-token')
    expect(auth.status).toBe('idle')
  })
})
