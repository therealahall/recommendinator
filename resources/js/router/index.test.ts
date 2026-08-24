import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import router from './index'

const TITLES = router.getRoutes().flatMap((route) => (route.name ? [route.meta.title] : []))

async function navigate(target: RouteLocationRaw) {
  await router.push(target)
  await nextTick()
}

describe('router', () => {
  it('names every route distinctly, so App can title the page it renders', () => {
    expect(TITLES.length).toBeGreaterThanOrEqual(3)
    expect(TITLES.filter(Boolean)).toHaveLength(TITLES.length)
    expect(new Set(TITLES).size).toBe(TITLES.length)
  })

  it('lands the bare / redirect on a named route rather than an untitled one', async () => {
    await navigate({ name: 'settings' })
    await navigate('/')
    expect(router.currentRoute.value.meta.title).toBeTruthy()
  })

  it('still moves focus to the main landmark after navigating', async () => {
    await navigate({ name: 'settings' })
    document.body.innerHTML = '<main id="main-content" tabindex="-1"></main>'
    await navigate({ name: 'library' })
    expect(document.activeElement).toBe(document.getElementById('main-content'))
  })
})
