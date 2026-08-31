import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ProfilePanel from './ProfilePanel.vue'

// What `GET /profile` answers for an account that has never regenerated one:
// a 200 with every field empty, not a 404.
const NEVER_GENERATED = {
  user_id: 1,
  genre_affinities: {},
  theme_preferences: [],
  anti_preferences: [],
  cross_media_patterns: [],
  generated_at: null,
}

// What both `GET /profile` and `POST /profile/regenerate` answer once the user
// has regenerated on a library with nothing rated yet: a saved profile, so
// generated_at is set, with every collection still empty.
const GENERATED_BUT_EMPTY = {
  ...NEVER_GENERATED,
  generated_at: '2026-08-13T12:00:00',
}

// One rated book whose review mentions a theme: MIN_ITEMS_PER_GENRE is 2, so
// the genre affinity it would have joined never clears the floor.
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
    // Reported: an empty bordered block on Preferences. Keying on generated_at
    // alone leaves it, because the panel's own button saves an empty profile
    // and every later visit then renders none of its three sections.
    mockGet.mockResolvedValue(GENERATED_BUT_EMPTY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('No profile generated')
  })

  it('still says no profile has been generated right after an empty regenerate', async () => {
    // Regenerate assigns its response unasked, so a fix applied only where the
    // profile is loaded leaves the click itself blanking the panel.
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockResolvedValue(GENERATED_BUT_EMPTY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('No profile generated')
  })

  it('renders a profile carrying nothing but themes', async () => {
    // Reported: the empty bordered block again. The store counted
    // theme_preferences as content while the panel rendered no section for it.
    mockGet.mockResolvedValue(THEMES_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('immersive')
    expect(wrapper.text()).not.toContain('No profile generated')
  })

  it('says a regenerate finished rather than clearing the region', async () => {
    // Success blanked the region, and clearing one announces nothing: the tags
    // above changed under the operator with no word that the run had ended.
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
    // The store swallowed the rejection, so a 500 read exactly like a success
    // on an empty library: the panel still said no profile was generated.
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
