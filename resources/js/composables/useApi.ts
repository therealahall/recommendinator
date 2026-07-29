const API_BASE = '/api'

interface ApiOptions extends RequestInit {
  params?: Record<string, string | number | boolean | undefined>
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    /** Parsed error response body, when the server returned JSON (e.g. a 422
     *  `{ detail: { key, reason } }` validation payload). Undefined otherwise. */
    public body?: unknown,
  ) {
    super(`${status} ${statusText}`)
    this.name = 'ApiError'
  }
}

/**
 * The server's `detail` string for a failed request, when there is one.
 *
 * Never use `ApiError.message` in user-facing copy: it is built from the status
 * line, so it reads "404 Not Found" while the explanation the user needs sits
 * in `detail`. FastAPI's own request validation sends `detail` as an array of
 * error objects instead — diagnostic output, not copy, and `[object Object]` in
 * a banner if rendered — so only a non-empty string is returned.
 */
export function apiErrorDetail(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined
  const { body } = error
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return undefined
  }
  const { detail } = body
  return typeof detail === 'string' && detail !== '' ? detail : undefined
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
): string {
  let url = `${API_BASE}${path}`
  if (params) {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        searchParams.set(key, String(value))
      }
    }
    const qs = searchParams.toString()
    if (qs) url += `?${qs}`
  }
  return url
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { params, ...fetchOptions } = options
  const url = buildUrl(path, params)

  const headers: HeadersInit = { ...fetchOptions.headers }
  // FormData sets its own multipart Content-Type (with boundary); only force
  // JSON for plain-object bodies.
  if (
    fetchOptions.body !== undefined &&
    !(fetchOptions.body instanceof FormData)
  ) {
    ;(headers as Record<string, string>)['Content-Type'] = 'application/json'
  }

  const response = await fetch(url, { ...fetchOptions, headers })

  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = undefined
    }
    throw new ApiError(response.status, response.statusText, body)
  }

  const contentType = response.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    return response.json()
  }

  return response.text() as unknown as T
}

export function useApi() {
  return {
    get<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
      return request<T>(path, { method: 'GET', params })
    },

    post<T>(path: string, body?: unknown) {
      return request<T>(path, {
        method: 'POST',
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    },

    put<T>(path: string, body?: unknown) {
      return request<T>(path, {
        method: 'PUT',
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    },

    patch<T>(path: string, body?: unknown) {
      return request<T>(path, {
        method: 'PATCH',
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    },

    postForm<T>(path: string, body: FormData) {
      return request<T>(path, { method: 'POST', body })
    },

    delete<T>(path: string) {
      return request<T>(path, { method: 'DELETE' })
    },

    /** Return raw Response for SSE / streaming endpoints */
    raw(path: string, options: ApiOptions = {}) {
      const { params, ...fetchOptions } = options
      return fetch(buildUrl(path, params), fetchOptions)
    },
  }
}
