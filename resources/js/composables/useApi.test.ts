import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useApi, ApiError, apiErrorDetail } from './useApi'

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

function stubFetch() {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })
}

describe('useApi request bodies', () => {
  stubFetch()

  function headersOfLastCall(): Record<string, string> {
    const calls = vi.mocked(fetch).mock.calls
    const [, init] = calls[calls.length - 1]
    return (init?.headers ?? {}) as Record<string, string>
  }

  it('posts a FormData body untouched and lets the browser set Content-Type', async () => {
    // Regression: forcing `application/json` on every body sent the multipart
    // upload without its generated boundary, so Starlette could not parse the
    // form and every real file import failed with a 400.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { message: 'ok' }))
    const form = new FormData()
    form.set('source', 'csv_import')

    await useApi().postForm('/import', form)

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/import')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(form)
    expect(headersOfLastCall()).not.toHaveProperty('Content-Type')
  })

  it('still forces application/json for a plain-object body', async () => {
    // The mirror of the test above: without it, dropping the header entirely
    // would also pass and every JSON endpoint would silently lose its type.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, {}))

    await useApi().post('/update', { source: 'steam' })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect(init?.body).toBe(JSON.stringify({ source: 'steam' }))
    expect(headersOfLastCall()['Content-Type']).toBe('application/json')
  })

  it('sets no Content-Type on a bodyless request', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, []))

    await useApi().get('/import/sources')

    expect(headersOfLastCall()).not.toHaveProperty('Content-Type')
  })
})

describe('useApi ApiError', () => {
  stubFetch()

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

describe('apiErrorDetail', () => {
  it('returns the server detail string', () => {
    const error = new ApiError(404, 'Not Found', {
      detail: 'Source has not been migrated to the database.',
    })

    // The whole point of the helper: `error.message` is "404 Not Found", which
    // tells the user nothing, while `detail` says where the entry really is.
    expect(error.message).toBe('404 Not Found')
    expect(apiErrorDetail(error)).toBe(
      'Source has not been migrated to the database.',
    )
  })

  it.each([
    ['a non-ApiError', new Error('network down')],
    ['no body at all', new ApiError(500, 'Internal Server Error')],
    ['a body without detail', new ApiError(400, 'Bad Request', { error: 'x' })],
    ['an empty detail', new ApiError(400, 'Bad Request', { detail: '' })],
    // FastAPI request validation: an array of error objects, which would
    // render as "[object Object]".
    [
      'a non-string detail',
      new ApiError(422, 'Unprocessable Entity', {
        detail: [{ loc: ['body', 'source'], msg: 'Field required' }],
      }),
    ],
  ])('returns undefined for %s', (_case, error) => {
    expect(apiErrorDetail(error)).toBeUndefined()
  })
})
