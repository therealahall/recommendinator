import { useAuthStore } from '@/stores/auth'

const API_BASE = '/api'

// `headers` is narrowed from HeadersInit: every caller passes a plain object,
// and the array and Headers forms cannot be spread into one.
interface ApiOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
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
    super(stringDetail(body) ?? `${status} ${statusText}`)
    this.name = 'ApiError'
  }
}

/** FastAPI's `{"detail": "..."}`, the one part of an error response written for
 *  the user to read. Validation payloads put an object there instead, which is
 *  no use as a message. */
function stringDetail(body: unknown): string | undefined {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return undefined
  // A blank detail is still a string, so it would win the `??` above and leave
  // the message empty — the page renders its prefix and nothing after it.
  if (typeof body.detail !== 'string' || body.detail.trim() === '') return undefined
  return body.detail
}

/** Read a failed response into an ApiError. Exported for the SSE callers, which
 *  hold the raw Response and would otherwise each re-invent the parse. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = undefined
  }
  return new ApiError(response.status, response.statusText, body)
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

/** The single door out to /api, so the streaming path cannot miss the token or
 *  the refusal. Callers swallow their own errors, so an unhandled 401 strands
 *  the user in a half-empty app with no way back to the gate. */
async function apiFetch(path: string, options: ApiOptions): Promise<Response> {
  const auth = useAuthStore()
  const { params, headers, ...fetchOptions } = options

  const merged: Record<string, string> = { ...headers }
  if (auth.token) {
    merged['Authorization'] = `Bearer ${auth.token}`
  }

  const response = await fetch(buildUrl(path, params), { ...fetchOptions, headers: merged })

  if (response.status === 401) {
    auth.reject()
  }

  return response
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = { ...options.headers }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await apiFetch(path, { ...options, headers })

  if (!response.ok) {
    throw await errorFromResponse(response)
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

    post<T>(path: string, body?: unknown, params?: ApiOptions['params']) {
      return request<T>(path, {
        method: 'POST',
        body: body !== undefined ? JSON.stringify(body) : undefined,
        params,
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

    delete<T>(path: string, params?: ApiOptions['params']) {
      return request<T>(path, { method: 'DELETE', params })
    },

    /** Return raw Response for SSE / streaming endpoints */
    raw(path: string, options: ApiOptions = {}) {
      return apiFetch(path, options)
    },
  }
}
