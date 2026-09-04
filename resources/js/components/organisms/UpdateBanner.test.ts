import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import UpdateBanner from './UpdateBanner.vue'
import { useAppStore } from '@/stores/app'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({ get: vi.fn(), post: vi.fn() }),
}))

describe('UpdateBanner', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('names both recoveries, and offers no reload, when the served bundle is the old half', () => {
    const app = useAppStore()
    app.showUpdateBanner = true
    app.staleBundle = true

    const wrapper = mount(UpdateBanner)

    const commands = wrapper.findAll('code').map((node) => node.text())
    expect(commands).toContain('pnpm build')
    expect(commands).toContain('docker compose up -d --build --renew-anon-volumes')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('keeps the recovery message in one flex item, so it reflows at 320px', () => {
    const app = useAppStore()
    app.showUpdateBanner = true
    app.staleBundle = true

    const wrapper = mount(UpdateBanner)

    expect(wrapper.find('.update-banner').element.children).toHaveLength(1)
  })

  it('offers the reload that does fix a version deployed mid-session', () => {
    const app = useAppStore()
    app.showUpdateBanner = true

    const wrapper = mount(UpdateBanner)

    expect(wrapper.find('button').text()).toBe('Reload')
  })

  it('holds the drift region from mount, and gives each package its own item', async () => {
    const app = useAppStore()
    const wrapper = mount(UpdateBanner)

    expect(wrapper.find('[role="status"]').exists()).toBe(true)

    app.dependencyDrift = [
      { package: 'newdep', declared: '>=2.0', installed: null, message: 'newdep is missing' },
      { package: 'olddep', declared: '<3.0', installed: '4.0', message: 'olddep 4.0 is old' },
    ]
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('[role="status"] li').map((item) => item.text())).toEqual([
      'newdep is missing',
      'olddep 4.0 is old',
    ])
  })
})
