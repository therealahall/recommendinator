import { readFileSync } from 'node:fs'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'
import { deferredFetch, jsonResponse } from '@/testing/http'

// fetch is stubbed rather than useApi mocked: submitToken calls fetch itself, so
// that the store never imports the API layer that imports the store.
describe('useAuthStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts unauthenticated when nothing is stored', () => {
    const auth = useAuthStore()

    expect(auth.token).toBe('')
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.status).toBe('idle')
  })

  it('seeds the token from storage, so a reload does not ask again', () => {
    localStorage.setItem('apiToken', 'stored-token')

    const auth = useAuthStore()

    expect(auth.token).toBe('stored-token')
    expect(auth.isAuthenticated).toBe(true)
  })

  it('stays locked, and unpersisted, until the server answers', async () => {
    const answer = deferredFetch()
    const auth = useAuthStore()

    const submitted = auth.submitToken('candidate')

    expect(auth.status).toBe('verifying')
    expect(auth.isAuthenticated).toBe(false)
    // token holds verified tokens only; the candidate rides in the header.
    expect(auth.token).toBe('')
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/status')
    const headers = vi.mocked(fetch).mock.calls[0][1]?.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer candidate')

    answer(jsonResponse(200, { status: 'ready' }))

    expect(await submitted).toBe(true)
    expect(auth.status).toBe('idle')
    expect(auth.isAuthenticated).toBe(true)
  })

  it('persists a trimmed token, because a pasted one carries whitespace', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { status: 'ready' }))
    const auth = useAuthStore()

    await auth.submitToken('  pasted-token\n')

    expect(auth.token).toBe('pasted-token')
    expect(localStorage.getItem('apiToken')).toBe('pasted-token')
  })

  it('keeps nothing when the server refuses the token', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'nope' }))
    const auth = useAuthStore()

    expect(await auth.submitToken('wrong-token')).toBe(false)

    expect(auth.token).toBe('')
    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(auth.status).toBe('rejected')
  })

  it('distinguishes an unreachable server from a refused token', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const auth = useAuthStore()

    expect(await auth.submitToken('untested-token')).toBe(false)

    expect(auth.token).toBe('')
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(auth.status).toBe('unreachable')
  })

  it('treats a server error as unreachable, not as a refusal', async () => {
    // A 500 says nothing about the token, so the gate must not tell the user it
    // was wrong — but an unconfirmed token is still never kept.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(500, { detail: 'boom' }))
    const auth = useAuthStore()

    expect(await auth.submitToken('untested-token')).toBe(false)

    expect(auth.token).toBe('')
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(auth.status).toBe('unreachable')
  })

  it('ignores a second submission while one is in flight', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { status: 'ready' }))
    const auth = useAuthStore()

    const first = auth.submitToken('first-token')
    const second = auth.submitToken('second-token')

    expect(await second).toBe(false)
    expect(await first).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(auth.token).toBe('first-token')
  })

  it('clears the refusal once a later token is accepted', async () => {
    const auth = useAuthStore()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'nope' }))
    await auth.submitToken('wrong-token')

    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { status: 'ready' }))
    await auth.submitToken('right-token')

    expect(auth.status).toBe('idle')
    expect(auth.isAuthenticated).toBe(true)
  })

  it('never imports the API layer, which imports this store', () => {
    // useApi calls useAuthStore(), so importing it back is the cycle
    // DEVELOPMENT_PATTERNS bans. Asserted on the source because a cycle the
    // bundler has already resolved breaks at boot, not under test.
    const source = readFileSync(`${process.cwd()}/resources/js/stores/auth.ts`, 'utf8')

    // Unanchored: a relative '../composables/useApi' closes the same cycle.
    expect(source).not.toMatch(/composables\/useApi/)
  })

  it('drops a rejected token from storage and flags it for the gate', () => {
    localStorage.setItem('apiToken', 'revoked-token')
    const auth = useAuthStore()

    auth.reject()

    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(auth.status).toBe('rejected')
  })
})
