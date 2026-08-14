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

  it('re-enables Regenerate after the request fails', async () => {
    mockGet.mockResolvedValue(NEVER_GENERATED)
    mockPost.mockRejectedValue(new Error('boom'))

    const wrapper = mount(ProfilePanel)
    await flushPromises()

    const button = () => wrapper.findAll('button').find((b) => b.text().startsWith('Regenerate') || b.text() === 'Generating...')!
    await button().trigger('click')
    await flushPromises()

    expect(button().attributes('disabled')).toBeUndefined()
    expect(button().text()).toBe('Regenerate')
  })
})
