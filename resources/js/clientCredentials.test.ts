import { readdirSync, readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

const ROOT = `${process.cwd()}/resources`
const SELF = 'js/clientCredentials.test.ts'
const SCANNED = /\.(ts|vue|css|js|json|html)$/

/** A credential the page can read: a token in storage, or a header the SPA
 *  builds by hand. Both were how auth worked before the session cookie. */
const HAND_HELD_CREDENTIAL = /apiToken|Authorization\s*[:'"\]]|Bearer\s/

/** A request addressed by hand. Matched on the endpoint rather than on
 *  `fetch(`, which one store's own action is called. */
const OWN_REQUEST = /['"`]\/api\b|new EventSource\b/

// The API layer, and nothing else: the auth store went through its own fetch
// while a module cycle stood in the way, and no longer does.
const CREDENTIAL_CARRIERS = ['js/composables/useApi.ts']

// Neither opens a request from script: devServer names the prefix Vite proxies
// to FastAPI, and the export is a top-level navigation, which carries a
// same-site cookie by itself and could never have carried a header.
const NOT_A_CALLER = ['vite/devServer.ts', 'js/stores/library.ts']

function sourcesUnder(relative: string): string[] {
  return readdirSync(`${ROOT}/${relative}`, { withFileTypes: true }).flatMap((entry) => {
    const path = relative ? `${relative}/${entry.name}` : entry.name
    if (entry.isDirectory()) return sourcesUnder(path)
    return SCANNED.test(entry.name) ? [path] : []
  })
}

// Excluded from its own scan: the test spells out the shapes it bans.
const SOURCES = sourcesUnder('').filter((path) => path !== SELF)

// Tests stub fetch, and the stub lives in the testing helper beside them.
const SHIPPED = SOURCES.filter(
  (path) => !path.includes('.test.') && !path.startsWith('js/testing/'),
)

function read(path: string): string {
  return readFileSync(`${ROOT}/${path}`, 'utf8')
}

describe('client-held credentials', () => {
  it('scans a populated tree, so the guard below cannot pass on an empty list', () => {
    expect(SOURCES.length).toBeGreaterThan(50)
    expect(SOURCES).toContain('js/stores/auth.ts')
    expect(SOURCES).toContain('js/composables/useApi.ts')
    expect(SOURCES).toContain('css/base.css')
    expect(SOURCES).not.toContain(SELF)
  })

  it('recognises both shapes it bans, and leaves OAuth prose alone', () => {
    // A pattern that matched nothing would pass the scan against any tree.
    expect(HAND_HELD_CREDENTIAL.test("merged['Authorization'] = `Bearer ${token}`")).toBe(true)
    expect(HAND_HELD_CREDENTIAL.test("{ Authorization: 'Bearer x' }")).toBe(true)
    expect(HAND_HELD_CREDENTIAL.test("localStorage.getItem('apiToken')")).toBe(true)
    expect(HAND_HELD_CREDENTIAL.test('Paste the Authorization code from the response')).toBe(false)
  })

  it('holds no token and builds no auth header anywhere under resources/', () => {
    // The session is an httpOnly cookie the browser attaches itself. Anything
    // the SPA can read, script injected into the page can read too.
    const offenders = SOURCES.filter((path) => HAND_HELD_CREDENTIAL.test(read(path)))

    expect(offenders).toEqual([])
  })
})

describe('where a request may be opened', () => {
  it('scans the shipped tree, and finds the carriers in it', () => {
    expect(SHIPPED.length).toBeGreaterThan(50)
    expect(SHIPPED).toContain('js/stores/auth.ts')
    for (const carrier of CREDENTIAL_CARRIERS) expect(SHIPPED).toContain(carrier)
  })

  it('sends the auth calls through the same door as every other one', () => {
    // The one store that used to hold its own fetch: the cycle that forced it
    // is broken in the API layer now, so nothing else attaches the cookie.
    const source = read('js/stores/auth.ts')

    expect(source).toMatch(/@\/composables\/useApi/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    // Attaching the cookie is the API layer's job, and a second copy of that
    // decision is how the two drifted apart before.
    expect(source).not.toMatch(/credentials: 'include'/)
  })

  it.each(CREDENTIAL_CARRIERS)('%s addresses the API, and sends the session with it', (path) => {
    // An anchor for the scan below: a pattern matching no real call site would
    // pass it against any tree.
    expect(OWN_REQUEST.test(read(path))).toBe(true)
    expect(read(path)).toMatch(/credentials: 'include'/)
  })

  it('is addressed nowhere else, so no request can go out anonymous', () => {
    // Everything else routes through useApi, which attaches the cookie and
    // reads the 401 — a hand-rolled fetch or EventSource skips both.
    const skipped = [...CREDENTIAL_CARRIERS, ...NOT_A_CALLER]
    const offenders = SHIPPED.filter(
      (path) => !skipped.includes(path) && OWN_REQUEST.test(read(path)),
    )

    expect(offenders).toEqual([])
  })

  it('leaves the export a navigation, the one /api URL outside the carriers', () => {
    // Pins what the exemption above covers: a header could never ride a
    // download, and the cookie does.
    const apiUrls = read('js/stores/library.ts').match(/['"`]\/api[^'"`]*/g)

    expect(apiUrls).toEqual(['`/api/items/export?${params}'])
  })

  it('keeps no trace of the token gate the session replaced', () => {
    // Deleted, not orphaned: an import of it would break the build, and a
    // stylesheet rule for it would rot silently.
    expect(SOURCES.filter((path) => /TokenGate|token-gate/.test(read(path)))).toEqual([])
  })
})
