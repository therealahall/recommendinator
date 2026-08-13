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
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
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
    expect(labels).toEqual(['Recommendations', 'Library', 'Chat', 'Data', 'Preferences', 'Settings'])
  })

  it('hides chat when AI is disabled', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    // Chat should not be in the DOM (v-if removes it)
    const chatBtn = wrapper.findAll('.nav-item').find((n) => n.text().includes('Chat'))
    expect(chatBtn).toBeUndefined()
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

  it('sets aria-current="page" on active nav item only', async () => {
    const router = createTestRouter()
    await router.push('/library')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const navItems = wrapper.findAll('.nav-item')
    const libraryBtn = navItems.find((n) => n.text().includes('Library'))
    const recsBtn = navItems.find((n) => n.text().includes('Recommendations'))
    expect(libraryBtn!.attributes('aria-current')).toBe('page')
    expect(recsBtn!.attributes('aria-current')).toBeUndefined()
  })

  it('decorative SVGs have aria-hidden', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      global: { plugins: [router] },
    })

    const svgs = wrapper.findAll('.nav-item svg')
    expect(svgs.length).toBeGreaterThan(0)
    svgs.forEach((svg) => {
      expect(svg.attributes('aria-hidden')).toBe('true')
    })
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

  it('names the signed-in user by their display name', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 1, username: 'alice', display_name: 'Alice' } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('[data-testid="sidebar-user"]').text()).toContain('Alice')
  })

  it('falls back to the username when there is no display name', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 2, username: 'bob', display_name: null } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.sidebar-user-name').text()).toBe('bob')
  })

  it('falls back to the username when the display name is the empty string', async () => {
    // SetupForm sends display_name: '' for an omitted one, so '' and not null
    // is what the first account is created with.
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 3, username: 'carol', display_name: '' } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.sidebar-user-name').text()).toBe('carol')
  })

  it('shows a name the alphabet does not fit in', async () => {
    // The account is named from a free-text field with no character class on
    // it, on either surface, so the row has to render whatever was typed.
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 4, username: 'aaron', display_name: 'Áaron 시 🎧' } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.sidebar-user-name').text()).toBe('Áaron 시 🎧')
  })

  it('offers no way to switch users', async () => {
    // A second person signs in with their own credentials; there is no account
    // this one can step into from here, so the footer is a label, not a control.
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, {
      props: { user: { id: 1, username: 'alice', display_name: 'Alice' } },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.sidebar-footer select').exists()).toBe(false)
    expect(wrapper.find('.sidebar-footer button').exists()).toBe(false)
    expect(wrapper.findAll('option')).toHaveLength(0)
  })

  it('waits rather than rendering a nameless row before the session resolves', async () => {
    const router = createTestRouter()
    await router.push('/recommendations')
    await router.isReady()

    const wrapper = mount(AppSidebar, { global: { plugins: [router] } })

    expect(wrapper.find('.sidebar-footer').exists()).toBe(false)
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
