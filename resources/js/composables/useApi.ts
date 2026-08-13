import { stringDetail } from '@/utils/apiDetail'

const API_BASE = '/api'

// `headers` is narrowed from HeadersInit: every caller passes a plain object,
// and the array and Headers forms cannot be spread into one.
interface ApiOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
  params?: Record<string, string | number | boolean | undefined>
  /** Set on a route that answers 401 for the request rather than for the
   *  session — changing a password refuses a wrong current one that way, and
   *  signing out there would cost a typo the whole screen. */
  sessionSurvives401?: boolean
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

/** The single door out to /api, so the streaming path cannot miss the refusal.
 *  Callers swallow their own errors, so an unhandled 401 strands the user in a
 *  half-empty app with no way back to the sign-in screen. */
async function apiFetch(path: string, options: ApiOptions): Promise<Response> {
  const { params, headers, sessionSurvives401, ...fetchOptions } = options

  const response = await fetch(buildUrl(path, params), {
    ...fetchOptions,
    headers,
    // The session is an httpOnly cookie, so nothing here can attach it by hand
    // and a request that omits it is anonymous.
    credentials: 'include',
  })

  if (response.status === 401 && !sessionSurvives401) {
    // Imported here rather than at the top: the auth store sends its own calls
    // through this module, and a module-level import back closes that cycle.
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().reject()
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

    put<T>(path: string, body?: unknown, options: Pick<ApiOptions, 'sessionSurvives401'> = {}) {
      return request<T>(path, {
        ...options,
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
