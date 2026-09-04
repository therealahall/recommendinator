// @vitest-environment node
import { readFileSync } from 'fs'
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
  it('serves on 5173 and proxies to FastAPI on 18473', () => {
    const options = devServerOptions({})

    expect(options.port).toBe(5173)
    expect(proxyFor(options, '/api').target).toBe(DEFAULT_TARGET)
    expect(proxyFor(options, '/static/themes').target).toBe(DEFAULT_TARGET)
    expect(proxyFor(options, '/static/private-themes').target).toBe(DEFAULT_TARGET)
  })

  it('binds IPv4 loopback, which is the address Caddy proxies to', () => {
    expect(devServerOptions({}).host).toBe('127.0.0.1')
  })

  it('leaves HMR entirely up to Vite', () => {
    const options = devServerOptions({})

    expect(options.hmr).toEqual({ clientPort: undefined, protocol: undefined })
  })

  it('treats an empty value as unset', () => {
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
    const options = devServerOptions({ DEV_SERVER_API_TARGET: 'http://127.0.0.1:9000' })

    expect(proxyFor(options, '/api').target).toBe('http://127.0.0.1:9000')
    expect(proxyFor(options, '/static/themes').target).toBe('http://127.0.0.1:9000')
  })

  it('points the HMR client at the public port over TLS', () => {
    const options = devServerOptions({
      DEV_SERVER_PORT: '3000',
      DEV_SERVER_HMR_CLIENT_PORT: '443',
      DEV_SERVER_HMR_PROTOCOL: 'wss',
    })

    expect(options.port).toBe(3000)
    expect(options.hmr).toEqual({ clientPort: 443, protocol: 'wss' })
  })

  it('never pins the HMR host', () => {
    const options = devServerOptions({
      DEV_SERVER_HMR_CLIENT_PORT: '443',
      DEV_SERVER_HMR_PROTOCOL: 'wss',
    })

    expect(options.hmr).not.toHaveProperty('host')
  })

  it.each(['DEV_SERVER_PORT', 'DEV_SERVER_HMR_CLIENT_PORT'])(
    'rejects a %s that is not a port',
    (name) => {
      expect(() => devServerOptions({ [name]: '3000;' })).toThrow(name)
      expect(() => devServerOptions({ [name]: '99999' })).toThrow(name)
    },
  )
})

describe('the watcher', () => {
  it('does not watch the agent worktrees', () => {
    const ignored = devServerOptions({}).watch?.ignored as string[]

    expect(ignored).toContain('**/.claude/worktrees/**')
  })

  it('does not watch the caches a worktree brings with it', () => {
    const ignored = devServerOptions({}).watch?.ignored as string[]

    expect(ignored).toEqual(
      expect.arrayContaining(['**/.mypy_cache/**', '**/.pytest_cache/**', '**/.venv/**']),
    )
  })

  it('still watches the frontend source it exists to serve', () => {
    const ignored = devServerOptions({}).watch?.ignored as string[]

    expect(ignored.some((pattern) => pattern.includes('resources'))).toBe(false)
  })
})

describe('vite.config.ts wiring', () => {
  it('hands the dev server the proxied prefixes', async () => {
    const config = await resolveViteConfig()

    expect(Object.keys(config.server?.proxy ?? {})).toEqual([
      '/api',
      '/static/themes',
      '/static/private-themes',
    ])
  })

  it('stamps the bundle with the version pyproject declares', async () => {
    const stamped = (await resolveViteConfig()).define?.__BUNDLE_VERSION__
    const pyproject = readFileSync(new URL('../../pyproject.toml', import.meta.url), 'utf-8')

    expect(stamped).not.toBe('""')
    expect(pyproject).toContain(`\nversion = ${stamped}\n`)
  })

  it('reads variables that are not VITE_ prefixed', async () => {
    vi.stubEnv('DEV_SERVER_API_TARGET', 'http://127.0.0.1:9000')

    const config = await resolveViteConfig()

    expect(proxyFor(config.server ?? {}, '/api').target).toBe('http://127.0.0.1:9000')
  })
})
