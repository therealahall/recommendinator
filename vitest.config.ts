import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'resources/js'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['resources/**/*.{test,spec}.ts'],
  },
})
