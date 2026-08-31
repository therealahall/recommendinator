import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppSidebar from './AppSidebar.vue'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
  }),
}))

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', redirect: '/recommendations' },
      { path: '/recommendations', name: 'recommendations', component: { template: '<div />' } },
      { path: '/library', name: 'library', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
      { path: '/preferences', name: 'preferences', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
    ],
  })
}

describe('AppSidebar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('falls back to the username when the display name is the empty string', async () => {
    // SetupForm sends display_name: '' for an omitted one, so '' and not null
    // is what the first account is created with.
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 3, username: 'carol', display_name: '', password_updated_at: null } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.sidebar-user-name').text()).toBe('carol')
  })

  const SCREEN: Array<{ state: string; offscreen: boolean; hidden: boolean }> = [
    { state: 'closed on a phone', offscreen: true, hidden: true },
    { state: 'open, or on a desktop', offscreen: false, hidden: false },
  ]

  // Regression: the mobile sidebar was only slid off screen, so its six nav
  // buttons stayed tabbable and stayed in the accessibility tree — six Tab
  // presses landing on controls nobody could see.
  it.each(SCREEN)('is reachable only when it is $state', async ({ offscreen, hidden }) => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { offscreen },
      global: { plugins: [router] },
    })

    const aside = wrapper.find('.sidebar')
    expect(aside.attributes('aria-hidden')).toBe(hidden ? 'true' : undefined)
    expect('inert' in aside.attributes()).toBe(hidden)
  })

  it('holds no heading, so going off screen takes none out of the document', async () => {
    // The drawer goes inert and aria-hidden on a phone: any heading inside it
    // leaves the outline with the sidebar (WCAG 1.3.1).
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })

    expect(wrapper.findAll('h1, h2, h3, h4, h5, h6')).toHaveLength(0)
  })

  it('emits navigate on nav click', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const libraryBtn = wrapper.findAll('.nav-item').find((n) => n.text().includes('Library'))
    await libraryBtn!.trigger('click')

    expect(wrapper.emitted('navigate')).toBeTruthy()
  })
})
