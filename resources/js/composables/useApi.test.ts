import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { useApi, ApiError } from './useApi'

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `${status}`,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response
}

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
  })
})

function headersOf(call: number): Record<string, string> {
  return vi.mocked(fetch).mock.calls[call][1]?.headers as Record<string, string>
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
    useAuthStore().setToken('the-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().get('/users')

    expect(headersOf(0)['Authorization']).toBe('Bearer the-token')
  })

  it('sends it on streaming requests too, which bypass request()', async () => {
    useAuthStore().setToken('the-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().raw('/recommendations/stream', { method: 'GET' })

    expect(headersOf(0)['Authorization']).toBe('Bearer the-token')
  })

  it('keeps the caller-supplied headers alongside it', async () => {
    useAuthStore().setToken('the-token')
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
    const auth = useAuthStore()
    auth.setToken('stale-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'nope' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.isAuthenticated).toBe(false)
    expect(auth.rejected).toBe(true)
  })

  it('leaves the token alone on any other failure', async () => {
    const auth = useAuthStore()
    auth.setToken('good-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse(503, { detail: 'down' }))

    await expect(useApi().get('/users')).rejects.toBeInstanceOf(ApiError)

    expect(auth.token).toBe('good-token')
    expect(auth.rejected).toBe(false)
  })
})
