import { describe, it, expect, vi } from 'vitest'
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

  it('lands a bookmark of the old duplicates path on the page that replaced it', async () => {
    await navigate('/duplicates')

    expect(router.currentRoute.value.name).toBe('duplicates')
  })

  it('still moves focus to the main landmark after navigating', async () => {
    await navigate({ name: 'settings' })
    document.body.innerHTML = '<main id="main-content" tabindex="-1"></main>'
    await navigate({ name: 'library' })
    expect(document.activeElement).toBe(document.getElementById('main-content'))
  })

  it('moves that focus without scrolling the page under the reader', async () => {
    document.body.innerHTML = '<main id="main-content" tabindex="-1"></main>'
    const main = document.getElementById('main-content')
    if (main === null) throw new Error('no main landmark to focus')
    const focus = vi.spyOn(main, 'focus')

    await navigate({ name: 'data' })

    expect(focus).toHaveBeenCalledWith({ preventScroll: true })
  })

  it('opens a page at its top, and back at the offset it was left', () => {
    const { scrollBehavior } = router.options
    if (typeof scrollBehavior !== 'function') throw new Error('the router decides no scroll')
    const here = router.currentRoute.value

    expect(scrollBehavior(here, here, null)).toEqual({ top: 0 })
    expect(scrollBehavior(here, here, { top: 240, left: 0 })).toEqual({ top: 240, left: 0 })
  })
})
