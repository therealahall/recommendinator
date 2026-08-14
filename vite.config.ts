import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { devServerOptions } from './resources/vite/devServer'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
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
