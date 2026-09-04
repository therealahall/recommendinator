import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import {
  createRouter,
  createMemoryHistory,
  type RouteRecordRaw,
  type Router,
} from 'vue-router'
import appRouter from '@/router'
import AppNav from './AppNav.vue'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
  }),
}))

const ROUTES: RouteRecordRaw[] = appRouter.getRoutes().map((route) =>
  route.redirect
    ? ({ path: route.path, redirect: route.redirect } as RouteRecordRaw)
    : ({
        path: route.path,
        name: route.name,
        component: { template: '<div />' },
      } as RouteRecordRaw),
)

const DESTINATIONS = appRouter
  .getRoutes()
  .flatMap((route) => (route.name ? [String(route.name)] : []))

function createTestRouter() {
  return createRouter({ history: createMemoryHistory(), routes: ROUTES })
}

async function nav(router: Router, path: string, user?: { display_name: string }) {
  await router.push(path)
  await router.isReady()
  return mount(AppNav, {
    props: user
      ? { user: { id: 3, username: 'carol', password_updated_at: null, ...user } }
      : {},
    global: { plugins: [router] },
  })
}

function item(wrapper: ReturnType<typeof mount>, label: string) {
  const found = wrapper.findAll('.nav-item').find((link) => link.text().includes(label))
  if (!found) throw new Error(`no nav item labelled ${label}`)
  return found
}

describe('AppNav', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('falls back to the username when the display name is the empty string', async () => {
    const wrapper = await nav(createTestRouter(), '/recommendations', { display_name: '' })

    expect(wrapper.get('[data-testid="nav-user"]').text()).toContain('carol')
  })

  it('navigates by link, so a section has an address', async () => {
    const wrapper = await nav(createTestRouter(), '/recommendations')

    expect(item(wrapper, 'Library').attributes('href')).toContain('/library')
  })

  const CURRENT: Array<{ screen: string; path: string; marked: string; value: string }> = [
    { screen: 'the section it links to', path: '/library', marked: 'Library', value: 'page' },
    { screen: 'a section it only holds', path: '/library/duplicates', marked: 'Library', value: 'true' },
  ]

  it.each(CURRENT)('marks $screen', async ({ path, marked, value }) => {
    const wrapper = await nav(createTestRouter(), path)

    expect(item(wrapper, marked).attributes('aria-current')).toBe(value)
  })

  it.each(DESTINATIONS)('places %s under exactly one section', async (name) => {
    const wrapper = await nav(createTestRouter(), appRouter.resolve({ name }).path)

    expect(
      wrapper.findAll('.nav-item').filter((link) => link.attributes('aria-current')),
    ).toHaveLength(1)
  })

  it('offers no section of its own for duplicates', async () => {
    const wrapper = await nav(createTestRouter(), '/library')

    expect(wrapper.findAll('.nav-item').map((link) => link.attributes('href'))).not.toContain(
      appRouter.resolve({ name: 'duplicates' }).path,
    )
  })
})
