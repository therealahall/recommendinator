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

/** Taken from the real route table rather than copied out of it, so a route the
 *  app gains is a route this file has to find a home for. */
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
    // SetupForm sends display_name: '' for an omitted one, so '' and not null
    // is what the first account is created with.
    const wrapper = await nav(createTestRouter(), '/recommendations', { display_name: '' })

    expect(wrapper.get('[data-testid="nav-user"]').text()).toContain('carol')
  })

  // Regression: every section was a <button>, so nothing could be opened in a
  // new tab, copied, or read as a link by assistive tech.
  it('navigates by link, so a section has an address', async () => {
    const wrapper = await nav(createTestRouter(), '/recommendations')

    expect(item(wrapper, 'Library').attributes('href')).toContain('/library')
  })

  const CURRENT: Array<{ screen: string; path: string; marked: string; value: string }> = [
    { screen: 'the section it links to', path: '/library', marked: 'Library', value: 'page' },
    // Duplicates is a Library function with no rail item of its own, so without
    // this the rail says nothing at all about where the user is.
    { screen: 'a section it only holds', path: '/library/duplicates', marked: 'Library', value: 'true' },
  ]

  it.each(CURRENT)('marks $screen', async ({ path, marked, value }) => {
    const wrapper = await nav(createTestRouter(), path)

    expect(item(wrapper, marked).attributes('aria-current')).toBe(value)
  })

  // The rail links to five of the six routes, so the sixth is only ever marked
  // by a section that names it in `within`.
  it.each(DESTINATIONS)('places %s under exactly one section', async (name) => {
    const wrapper = await nav(createTestRouter(), appRouter.resolve({ name }).path)

    expect(
      wrapper.findAll('.nav-item').filter((link) => link.attributes('aria-current')),
    ).toHaveLength(1)
  })

  // Duplicates was a seventh rail item reached from nowhere else; it is now a
  // Library function, and a rail item for it would say otherwise.
  it('offers no section of its own for duplicates', async () => {
    const wrapper = await nav(createTestRouter(), '/library')

    expect(wrapper.text()).not.toContain('Duplicates')
  })
})
