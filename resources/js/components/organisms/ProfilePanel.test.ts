import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ProfilePanel from './ProfilePanel.vue'
import { formatDate } from '@/utils/format'

const NEVER_GENERATED = {
  user_id: 1,
  genre_affinities: [],
  author_affinities: [],
  theme_preferences: [],
  cross_media_patterns: [],
  has_content: false,
  generated_at: null,
}

const GENERATED_BUT_EMPTY = {
  ...NEVER_GENERATED,
  generated_at: '2026-08-13T12:00:00',
}

const THEMES_ONLY = {
  ...NEVER_GENERATED,
  theme_preferences: ['immersive'],
  has_content: true,
  generated_at: '2026-08-13T12:00:00',
}

const AUTHORS_ONLY = {
  ...NEVER_GENERATED,
  author_affinities: [{ author: 'Terry Brooks', score: 4.0 }],
  has_content: true,
  generated_at: '2026-08-13T12:00:00',
}

const A_DISLIKED_GENRE = {
  ...NEVER_GENERATED,
  genre_affinities: [
    { genre: 'science fiction', score: 4.5, anti: false },
    { genre: 'literary fiction', score: 2.0, anti: true },
  ],
  has_content: true,
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

  it('stops asking for a regenerate once an empty one has answered', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockResolvedValue(GENERATED_BUT_EMPTY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    const beforeRun = wrapper.get('.state-hint').text()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.state-hint').text()).not.toBe(beforeRun)
  })

  it('does not blame an unrated library for a regenerate that failed', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockRejectedValue(new Error('Profile generation failed'))

    const wrapper = mount(ProfilePanel)
    await flushPromises()
    const beforeRun = wrapper.get('.state-hint').text()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.state-hint').text()).toBe(beforeRun)
  })

  it('renders a profile carrying nothing but themes', async () => {
    mockGet.mockResolvedValue(THEMES_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('immersive')
    expect(wrapper.text()).not.toContain('No profile generated')
  })

  it('renders a profile carrying nothing but author affinities', async () => {
    mockGet.mockResolvedValue(AUTHORS_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.find('[data-testid="profile-empty"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Terry Brooks 4.0')
  })

  it('names a disliked genre once rather than in a second list', async () => {
    mockGet.mockResolvedValue(A_DISLIKED_GENRE)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    const badges = wrapper.findAll('.badge')
    expect(badges).toHaveLength(2)
    expect(wrapper.text()).toContain('literary fiction 2.0')
  })

  it("says in words, not in hue, that a genre is not the operator's style", async () => {
    mockGet.mockResolvedValue(A_DISLIKED_GENRE)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    const spoken = wrapper.findAll('.sr-only').map((node) => node.text())
    expect(spoken).toContain('literary fiction, rated 2.0 out of 5 on average — not your style')
    expect(spoken).toContain('science fiction, rated 4.5 out of 5 on average')
  })

  it('speaks an unscored anti-preference, which has no mean to read out', async () => {
    mockGet.mockResolvedValue({
      ...NEVER_GENERATED,
      genre_affinities: [{ genre: 'western', score: null, anti: true }],
      has_content: true,
    })

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.get('.sr-only').text()).toBe('western — not your style')
  })

  it('gives every badge row item boundaries a reader can hear', async () => {
    mockGet.mockResolvedValue({
      ...A_DISLIKED_GENRE,
      author_affinities: [{ author: 'Terry Brooks', score: 4.0 }],
      theme_preferences: ['immersive'],
    })

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.findAll('ul[role="list"] > li > .badge')).toHaveLength(
      wrapper.findAll('.badge').length,
    )
    for (const list of wrapper.findAll('ul[role="list"]')) {
      expect(list.attributes('aria-labelledby')).toBeTruthy()
    }
  })

  it('renders the whole bounded genre list rather than a shorter slice', async () => {
    mockGet.mockResolvedValue({
      ...NEVER_GENERATED,
      genre_affinities: Array.from({ length: 12 }, (_, index) => ({
        genre: `genre${index}`,
        score: 4,
        anti: false,
      })),
      has_content: true,
    })

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.findAll('.badge')).toHaveLength(12)
  })

  it('says when the profile it is showing was generated', async () => {
    mockGet.mockResolvedValue(THEMES_ONLY)

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    expect(wrapper.get('[data-testid="profile-generated"]').text()).toContain(
      formatDate('2026-08-13T12:00:00'),
    )
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
