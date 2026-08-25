import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { devServerOptions } from './resources/vite/devServer'

function versionThisBundleIsBuiltFrom(): string {
  try {
    const pyproject = readFileSync(resolve(__dirname, 'pyproject.toml'), 'utf-8')
    const table = pyproject.split(/^\[/m).find((section) => section.startsWith('project]'))
    return table?.match(/^version = "([^"]+)"/m)?.[1] ?? ''
  } catch {
    return ''
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  define: {
    __BUNDLE_VERSION__: JSON.stringify(versionThisBundleIsBuiltFrom()),
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'resources/js'),
    },
  },
  base: '/static/dist/',
  build: {
    outDir: resolve(__dirname, 'src/web/static/dist'),
    emptyOutDir: true,
    modulePreload: { polyfill: false },
  },
  // Empty prefix so the DEV_SERVER_* names are picked up from .env and the shell
  // alike. They deliberately avoid Vite's `VITE_` prefix: they configure the dev
  // server, and a prefixed name would also be inlined into the client bundle.
  server: devServerOptions(loadEnv(mode, __dirname, '')),
}))
