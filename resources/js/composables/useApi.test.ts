import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
