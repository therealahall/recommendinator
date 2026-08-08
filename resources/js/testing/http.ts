import { vi } from 'vitest'

/** Stand-in for a fetch Response. jsdom has no usable constructor, and the API
 *  layer reads only these four members. */
export function jsonResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `${status}`,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response
}

/** Hold the next fetch open so an in-flight state can be observed, and hand
 *  back the resolver that ends it. Requires a stubbed global fetch. */
export function deferredFetch(): (response: Response) => void {
  let answer: (response: Response) => void = () => {}
  vi.mocked(fetch).mockReturnValue(
    new Promise<Response>((resolve) => {
      answer = resolve
    }),
  )
  return (response) => answer(response)
}
