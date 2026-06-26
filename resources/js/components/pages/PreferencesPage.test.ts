import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreferencesPage from './PreferencesPage.vue'
import { useThemeStore } from '@/stores/theme'
import { usePreferencesStore } from '@/stores/preferences'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn().mockResolvedValue({
      scorer_weights: {},
      series_in_order: true,
      variety_penalty: 0.0,
      content_length_preferences: {},
      custom_rules: [],
      theme: 'nord',
    }),
    put: vi.fn().mockResolvedValue({}),
  }),
}))

describe('PreferencesPage information architecture', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders the heading outline Appearance -> Scoring -> Rules with Length/Custom rules sub-blocks', async () => {
    // Appearance only renders when themes are available.
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.findAll('h2').map((h) => h.text())).toEqual(['Preferences'])
    expect(wrapper.findAll('h3').map((h) => h.text())).toEqual([
      'Appearance',
      'Scoring',
      'Rules',
    ])
    expect(wrapper.findAll('h4').map((h) => h.text())).toEqual([
      'Length',
      'Custom rules',
    ])
  })

  it('marks the preferences card aria-busy while loading, then clears it', async () => {
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    const prefs = usePreferencesStore()
    await flushPromises()

    prefs.loading = true
    await nextTick()
    expect(wrapper.find('.card').attributes('aria-busy')).toBe('true')

    prefs.loading = false
    await nextTick()
    expect(wrapper.find('.card').attributes('aria-busy')).toBeUndefined()
  })

  it('no longer renders a "Toggles" section', async () => {
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.text()).not.toContain('Toggles')
  })
})
