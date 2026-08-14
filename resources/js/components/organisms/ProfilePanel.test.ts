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

  it('says no profile has been generated when the server sends an empty one', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('No profile generated')
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

  it('holds the Regenerate lock in aria while its request is out', async () => {
    // Native `disabled` unfocuses the button under the finger that pressed
    // Enter and drops the user on <body> (WCAG 2.4.3).
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    await wrapper.find('button').trigger('click')

    expect(wrapper.find('button').attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('button').element.disabled).toBe(false)
    expect(wrapper.get('[role="status"]').text()).toBe('Generating…')
  })

  it('ignores a second press while the first request is out', async () => {
    // What the native lock used to buy, now that the button stays pressable.
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await wrapper.find('button').trigger('click')

    expect(mockPost).toHaveBeenCalledTimes(1)
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

  it('mounts the live region before it has anything to announce', async () => {
    // A region inserted already populated is read as page content and skipped.
    mockGet.mockResolvedValue(NEVER_GENERATED)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    const region = wrapper.get('[role="status"]')
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('')
  })
})
