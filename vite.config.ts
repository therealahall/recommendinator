import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
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
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:18473',
        changeOrigin: true,
      },
      '/static/themes': {
        target: 'http://localhost:18473',
        changeOrigin: true,
      },
    },
  },
})
