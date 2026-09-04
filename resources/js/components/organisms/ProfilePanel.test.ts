import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ProfilePanel from './ProfilePanel.vue'

const NEVER_GENERATED = {
  user_id: 1,
  genre_affinities: {},
  theme_preferences: [],
  anti_preferences: [],
  cross_media_patterns: [],
  generated_at: null,
}

const GENERATED_BUT_EMPTY = {
  ...NEVER_GENERATED,
  generated_at: '2026-08-13T12:00:00',
}

const THEMES_ONLY = {
  ...NEVER_GENERATED,
  theme_preferences: ['immersive'],
  generated_at: '2026-08-13T12:00:00',
}

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  }),
}))

describe('ProfilePanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
  })

  it('says no profile has been generated when the saved one is empty', async () => {
    mockGet.mockResolvedValue(GENERATED_BUT_EMPTY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.find('[data-testid="profile-empty"]').exists()).toBe(true)
  })

  it('still says no profile has been generated right after an empty regenerate', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockResolvedValue(GENERATED_BUT_EMPTY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="profile-empty"]').exists()).toBe(true)
  })

  it('renders a profile carrying nothing but themes', async () => {
    mockGet.mockResolvedValue(THEMES_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('immersive')
    expect(wrapper.text()).not.toContain('No profile generated')
  })

  it('says a regenerate finished rather than clearing the region', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockResolvedValue(THEMES_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    const region = wrapper.get('[role="status"]')
    expect(region.text()).toBe('')

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(region.text()).toContain('regenerated')
  })

  it('announces a failed regenerate and unlocks the button', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockRejectedValue(new Error('Profile generation failed'))

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="status"]').text()).toBe('Profile generation failed')
    expect(wrapper.find('button').attributes('aria-disabled')).toBeUndefined()
  })
})
