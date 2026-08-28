import type { ProxyOptions, ServerOptions } from 'vite'

/** Port `pnpm dev` serves on when DEV_SERVER_PORT is unset. */
const DEFAULT_PORT = 5173

/** FastAPI origin the proxied prefixes go to when DEV_SERVER_API_TARGET is unset. */
const DEFAULT_API_TARGET = 'http://localhost:18473'

/** Prefixes FastAPI owns; the dev server serves everything else itself. */
const PROXIED_PREFIXES = ['/api', '/static/themes', '/static/private-themes']

/** Trees holding no frontend source. `.claude/worktrees` is the one that bites:
 *  each agent worktree is a whole checkout with its own caches, and a few
 *  exhaust the inotify limit, which Vite reports as a bare ENOSPC crash at
 *  startup. */
const UNWATCHED = [
  '**/.claude/worktrees/**',
  '**/.mypy_cache/**',
  '**/.pytest_cache/**',
  '**/.venv/**',
  '**/data/**',
]

type Env = Record<string, string | undefined>

function readPort(env: Env, name: string): number | undefined {
  const raw = env[name]
  if (!raw) return undefined

  const port = Number(raw)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} must be a TCP port between 1 and 65535, got "${raw}"`)
  }
  return port
}

/** The overrides exist for running `pnpm dev` behind a reverse proxy, where the
 *  port and scheme the browser talks to are not the ones Vite listens on. */
export function devServerOptions(env: Env): ServerOptions {
  const target = env.DEV_SERVER_API_TARGET || DEFAULT_API_TARGET

  return {
    // Vite's default `localhost` resolves to ::1 on this box, and Caddy dials
    // 127.0.0.1 — that mismatch is a 502 through the proxy.
    host: '127.0.0.1',
    port: readPort(env, 'DEV_SERVER_PORT') ?? DEFAULT_PORT,
    watch: { ignored: UNWATCHED },
    proxy: Object.fromEntries(
      PROXIED_PREFIXES.map((prefix): [string, ProxyOptions] => [
        prefix,
        { target, changeOrigin: true },
      ]),
    ),
    hmr: {
      // No `host` on purpose. A dev server behind a proxy is usually reachable
      // under several hostnames, and pinning one would point every other one's
      // websocket somewhere that browser cannot reach.
      clientPort: readPort(env, 'DEV_SERVER_HMR_CLIENT_PORT'),
      protocol: env.DEV_SERVER_HMR_PROTOCOL || undefined,
    },
  }
}
