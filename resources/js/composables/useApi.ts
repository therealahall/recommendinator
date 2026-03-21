const API_BASE = '/api'

interface ApiOptions extends RequestInit {
  params?: Record<string, string | number | boolean | undefined>
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
  ) {
    super(`${status} ${statusText}`)
    this.name = 'ApiError'
  }
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
  if (fetchOptions.body !== undefined) {
    ;(headers as Record<string, string>)['Content-Type'] = 'application/json'
  }

  const response = await fetch(url, { ...fetchOptions, headers })

  if (!response.ok) {
    throw new ApiError(response.status, response.statusText)
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
