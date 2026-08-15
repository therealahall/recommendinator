import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppSidebar from './AppSidebar.vue'

// Mock useApi
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
