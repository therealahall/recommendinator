/** FastAPI's `{"detail": "..."}`, the one part of an error response written for
 *  the user to read. Its own module because the auth store reads refusals too,
 *  and cannot import the API layer without closing a cycle. */
export function stringDetail(body: unknown): string | undefined {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return undefined
  // A blank detail is still a string, so it would win the `??` at the call site
  // and leave the message empty — the page renders its prefix and nothing after.
  if (typeof body.detail !== 'string' || body.detail.trim() === '') return undefined
  return body.detail
}
