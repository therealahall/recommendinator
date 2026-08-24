import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import router, { APP_NAME } from './index'

const ROUTE_NAMES = router.getRoutes().flatMap((route) => (route.name ? [route.name] : []))

async function navigate(target: RouteLocationRaw) {
  document.title = ''
  await router.push(target)
  await nextTick()
}

describe('router', () => {
  it('gives every route a distinct title ending in the app name', async () => {
    const titles = new Set<string>()
    for (const name of ROUTE_NAMES) {
      await navigate({ name })
      expect(document.title.endsWith(APP_NAME)).toBe(true)
      expect(document.title.length).toBeGreaterThan(APP_NAME.length)
      titles.add(document.title)
    }
    expect(ROUTE_NAMES.length).toBeGreaterThanOrEqual(3)
    expect(titles.size).toBe(ROUTE_NAMES.length)
  })

  it('titles the route the bare / redirect lands on', async () => {
    await navigate({ name: 'settings' })
    await navigate('/')
    expect(document.title.endsWith(APP_NAME)).toBe(true)
    expect(document.title).not.toBe(APP_NAME)
  })

  it('still moves focus to the main landmark after navigating', async () => {
    await navigate({ name: 'settings' })
    document.body.innerHTML = '<main id="main-content" tabindex="-1"></main>'
    await navigate({ name: 'library' })
    expect(document.activeElement).toBe(document.getElementById('main-content'))
  })
})
