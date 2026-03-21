import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppSidebar from './AppSidebar.vue'
import { useAppStore } from '@/stores/app'

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
      { path: '/chat', name: 'chat', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
      { path: '/preferences', name: 'preferences', component: { template: '<div />' } },
    ],
  })
}

describe('AppSidebar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders all nav items', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const app = useAppStore()
    app.features.ai_enabled = true

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const navItems = wrapper.findAll('.nav-item')
    const labels = navItems.map((n) => n.text().trim())
    expect(labels).toEqual(['Recommendations', 'Library', 'Chat', 'Data', 'Preferences'])
  })

  it('hides chat when AI is disabled', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    // Chat should be hidden (v-show renders but display: none)
    const chatBtn = wrapper.findAll('.nav-item').find((n) => n.text().includes('Chat'))
    expect(chatBtn).toBeDefined()
    expect(chatBtn!.attributes('style')).toContain('display: none')
  })

  it('highlights active route', async () => {
    const router = createTestRouter()
    await router.push('/library')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const libraryBtn = wrapper.findAll('.nav-item').find((n) => n.text().includes('Library'))
    expect(libraryBtn!.classes()).toContain('active')
  })

  it('renders version when available', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const app = useAppStore()
    app.version = '1.0.0'

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    expect(wrapper.find('.version-label').text()).toBe('v1.0.0')
  })

  it('renders user select with users', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const app = useAppStore()
    app.users = [
      { id: 1, username: 'alice', display_name: 'Alice' },
      { id: 2, username: 'bob', display_name: null },
    ]

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const options = wrapper.findAll('option')
    expect(options.length).toBe(2)
    expect(options[0].text()).toBe('Alice')
    expect(options[1].text()).toBe('bob')
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
