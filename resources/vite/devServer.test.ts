// @vitest-environment node
//
// Build-time config, no DOM involved — and loading vite.config.ts pulls in
// esbuild, which refuses to run against jsdom's TextEncoder.
import type { ProxyOptions, ServerOptions, UserConfig } from 'vite'
import { describe, it, expect, vi, afterEach } from 'vitest'

import { devServerOptions } from './devServer'
import viteConfig from '../../vite.config'

const DEFAULT_TARGET = 'http://localhost:18473'

function proxyFor(server: ServerOptions, prefix: string): ProxyOptions {
  const entry = server.proxy?.[prefix]
  if (entry === undefined || typeof entry === 'string') {
    throw new Error(`${prefix} is not proxied with options`)
  }
  return entry
}

async function resolveViteConfig(): Promise<UserConfig> {
  if (typeof viteConfig !== 'function') {
    throw new Error('vite.config.ts must export a config function to read the env')
  }
  return await viteConfig({ command: 'serve', mode: 'development' })
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('dev server defaults', () => {
  // The committed defaults are the contract with every contributor: none of
  // them has the reverse proxy these env vars exist for, so an unset
  // environment must reproduce the hardcoded config this replaced exactly.
  it('serves on 5173 and proxies to FastAPI on 18473', () => {
    const options = devServerOptions({})

    expect(options.port).toBe(5173)
    expect(proxyFor(options, '/api').target).toBe(DEFAULT_TARGET)
    expect(proxyFor(options, '/static/themes').target).toBe(DEFAULT_TARGET)
  })

  it('leaves HMR entirely up to Vite', () => {
    // Vite reads every hmr field with `?.` + `||`, so undefined fields behave
    // exactly like no `hmr` key at all — the client keeps deriving its port
    // and protocol from the server it was served by.
    const options = devServerOptions({})

    expect(options.hmr).toEqual({ clientPort: undefined, protocol: undefined })
  })

  it('treats an empty value as unset', () => {
    // A `.env` file written as `DEV_SERVER_PORT=` yields an empty string rather
    // than an absent key. Falling through to the default is the only sane
    // reading; `Number('') === 0` would otherwise hand Vite port 0.
    const options = devServerOptions({
      DEV_SERVER_PORT: '',
      DEV_SERVER_API_TARGET: '',
      DEV_SERVER_HMR_CLIENT_PORT: '',
      DEV_SERVER_HMR_PROTOCOL: '',
    })

    expect(options.port).toBe(5173)
    expect(proxyFor(options, '/api').target).toBe(DEFAULT_TARGET)
    expect(options.hmr).toEqual({ clientPort: undefined, protocol: undefined })
  })
})

describe('dev server overrides', () => {
  it('moves the listen port', () => {
    expect(devServerOptions({ DEV_SERVER_PORT: '3000' }).port).toBe(3000)
  })

  it('retargets every proxied prefix at once', () => {
    // Both prefixes hit the same backend, so one variable has to move both.
    // Splitting them would let /api and /static/themes drift apart.
    const options = devServerOptions({ DEV_SERVER_API_TARGET: 'http://127.0.0.1:9000' })

    expect(proxyFor(options, '/api').target).toBe('http://127.0.0.1:9000')
    expect(proxyFor(options, '/static/themes').target).toBe('http://127.0.0.1:9000')
  })

  it('points the HMR client at the public port over TLS', () => {
    // The shape of every reverse-proxy setup: Vite listens on one port, the
    // browser only ever sees the proxy's, and the websocket has to follow the
    // page's scheme or the browser blocks it as mixed content.
    const options = devServerOptions({
      DEV_SERVER_PORT: '3000',
      DEV_SERVER_HMR_CLIENT_PORT: '443',
      DEV_SERVER_HMR_PROTOCOL: 'wss',
    })

    expect(options.port).toBe(3000)
    expect(options.hmr).toEqual({ clientPort: 443, protocol: 'wss' })
  })

  it('never pins the HMR host', () => {
    // Load-bearing omission, not an oversight. A dev server behind a proxy is
    // typically reachable under several hostnames, and any single `hmr.host`
    // would send every other one's websocket to an address that browser cannot
    // reach. Unset, the client falls back to location.hostname, which is right
    // for all of them. Asserted so nobody "fixes" the gap by adding one.
    const options = devServerOptions({
      DEV_SERVER_HMR_CLIENT_PORT: '443',
      DEV_SERVER_HMR_PROTOCOL: 'wss',
    })

    expect(options.hmr).not.toHaveProperty('host')
  })

  it.each(['DEV_SERVER_PORT', 'DEV_SERVER_HMR_CLIENT_PORT'])(
    'rejects a %s that is not a port',
    (name) => {
      // A typo'd port would otherwise be silently swallowed and the dev server
      // would come up on the default, which looks like the proxy is broken.
      expect(() => devServerOptions({ [name]: '3000;' })).toThrow(name)
      expect(() => devServerOptions({ [name]: '99999' })).toThrow(name)
    },
  )
})

describe('vite.config.ts wiring', () => {
  // These assert only what no environment can change, because the config
  // function really does read the developer's .env — asserting a default value
  // here would fail on any machine that has one.
  it('hands the dev server the proxied prefixes', async () => {
    const config = await resolveViteConfig()

    expect(Object.keys(config.server?.proxy ?? {})).toEqual(['/api', '/static/themes'])
  })

  it('reads variables that are not VITE_ prefixed', async () => {
    // Vite's loadEnv filters by prefix and defaults to `VITE_`. With that
    // default these variables would be dropped on the floor and the config
    // would silently keep its defaults — and any name we did expose would be
    // baked into the client bundle. Hence the empty prefix.
    vi.stubEnv('DEV_SERVER_API_TARGET', 'http://127.0.0.1:9000')

    const config = await resolveViteConfig()

    expect(proxyFor(config.server ?? {}, '/api').target).toBe('http://127.0.0.1:9000')
  })
})
