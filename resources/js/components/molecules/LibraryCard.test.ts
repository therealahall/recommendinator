import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryCard from './LibraryCard.vue'

const baseItem = {
  id: 'test-1',
  db_id: 1,
  title: 'Test Book',
  author: 'Author',
  content_type: 'book',
  status: 'unread',
  rating: null,
  review: null,
  source: 'goodreads',
  ignored: false,
  seasons_watched: null,
  total_seasons: null,
  enriched: true,
  genres: [],
  tags: [],
  description: null,
}

describe('LibraryCard', () => {
  it('renders the type and status pills in the left meta zone', () => {
    const wrapper = mount(LibraryCard, { props: { item: baseItem } })
    const tags = wrapper.find('.library-meta-tags')
    expect(tags.exists()).toBe(true)
    expect(tags.find('.badge-type').text()).toBe('Book')
    expect(tags.find('.badge-status').text()).toBe('Unread')
  })

  it('renders the rating as a non-badge element with five star slots', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, rating: 3 } } })

    // Rating is no longer a badge.
    expect(wrapper.find('.badge-rating').exists()).toBe(false)

    const rating = wrapper.find('.rating-stars')
    expect(rating.exists()).toBe(true)

    const stars = rating.findAll('.star')
    expect(stars.length).toBe(5)
    expect(rating.findAll('.star.filled').length).toBe(3)
    expect(rating.findAll('.star.empty').length).toBe(2)

    // The filled-vs-empty glyph distinction is the whole visual rationale, so
    // assert the actual rendered characters to catch an accidental template swap.
    expect(rating.find('.star.filled').text()).toBe('★')
    expect(rating.find('.star.empty').text()).toBe('☆')

    // Visible numeric value and screen-reader text.
    expect(rating.find('.value').text()).toBe('3/5')
    expect(rating.find('.sr-only').text()).toBe('Rated 3 out of 5')

    // The glyph wrapper and numeric value are hidden from assistive tech so the
    // rating is announced once via the sr-only text, not duplicated.
    const glyphWrapper = rating.find('.star').element.parentElement
    expect(glyphWrapper?.getAttribute('aria-hidden')).toBe('true')
    expect(rating.find('.value').attributes('aria-hidden')).toBe('true')
  })

  it('fills exactly one star at the minimum rating of 1', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, rating: 1 } } })
    const rating = wrapper.find('.rating-stars')
    expect(rating.findAll('.star.filled').length).toBe(1)
    expect(rating.findAll('.star.empty').length).toBe(4)
    expect(rating.find('.value').text()).toBe('1/5')
    expect(rating.find('.sr-only').text()).toBe('Rated 1 out of 5')
  })

  it('fills all five stars at the maximum rating of 5', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, rating: 5 } } })
    const rating = wrapper.find('.rating-stars')
    expect(rating.findAll('.star.filled').length).toBe(5)
    expect(rating.findAll('.star.empty').length).toBe(0)
    expect(rating.find('.value').text()).toBe('5/5')
    expect(rating.find('.sr-only').text()).toBe('Rated 5 out of 5')
  })

  it('renders five empty stars at a rating of 0', () => {
    // Documents the `v-if="item.rating !== null"` render-guard boundary: a 0
    // rating still renders. The backend constrains rating to 1-5, so this is not
    // a reachable backend state, only a guard-behavior assertion.
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, rating: 0 } } })
    const rating = wrapper.find('.rating-stars')
    expect(rating.exists()).toBe(true)
    expect(rating.findAll('.star.filled').length).toBe(0)
    expect(rating.findAll('.star.empty').length).toBe(5)
    expect(rating.find('.value').text()).toBe('0/5')
    expect(rating.find('.sr-only').text()).toBe('Rated 0 out of 5')
  })

  it('renders no rating element for an unrated item', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, rating: null } } })
    expect(wrapper.find('.rating-stars').exists()).toBe(false)
    // The meta row still renders with its pills.
    expect(wrapper.find('.library-meta-tags .badge-type').exists()).toBe(true)
  })

  it('shows the "Not enriched" marker in the state caption line, not the pill row', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, enriched: false } } })
    const state = wrapper.find('.library-state.not-enriched')
    expect(state.exists()).toBe(true)
    // The decorative .dot span carries no text, so the label is deterministic.
    expect(state.text()).toBe('Not enriched')
    // Not a badge.
    expect(wrapper.find('.badge-enrichment').exists()).toBe(false)
  })

  it('shows the "Ignored" marker in the state caption line', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, ignored: true } } })
    const state = wrapper.find('.library-state.ignored')
    expect(state.exists()).toBe(true)
    expect(state.text()).toBe('Ignored')
    expect(wrapper.find('.badge-ignored').exists()).toBe(false)
  })

  it('shows both state markers together when the item is not enriched and ignored', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, enriched: false, ignored: true } },
    })
    expect(wrapper.find('.library-state.not-enriched').text()).toBe('Not enriched')
    expect(wrapper.find('.library-state.ignored').text()).toBe('Ignored')
  })

  it('renders no rating and no state line for an enriched, non-ignored, unrated item', () => {
    const wrapper = mount(LibraryCard, { props: { item: baseItem } })
    expect(wrapper.find('.rating-stars').exists()).toBe(false)
    expect(wrapper.find('.library-state-lines').exists()).toBe(false)
    expect(wrapper.find('.library-state').exists()).toBe(false)
  })

  it('emits edit with the db_id when the Edit button is clicked', async () => {
    const wrapper = mount(LibraryCard, { props: { item: baseItem } })
    const buttons = wrapper.findAll('.library-item-actions button')
    const edit = buttons.find((b) => b.text() === 'Edit')
    expect(edit).toBeDefined()
    await edit!.trigger('click')
    expect(wrapper.emitted('edit')).toEqual([[1]])
  })

  it('emits toggleIgnore to ignore a non-ignored item and labels the button "Ignore"', async () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, ignored: false } },
    })
    const buttons = wrapper.findAll('.library-item-actions button')
    const action = buttons.find((b) => b.text() === 'Ignore')
    expect(action).toBeDefined()
    await action!.trigger('click')
    expect(wrapper.emitted('toggleIgnore')).toEqual([[1, true]])
  })

  it('emits toggleIgnore to unignore an ignored item and labels the button "Unignore"', async () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, ignored: true } },
    })
    const buttons = wrapper.findAll('.library-item-actions button')
    const action = buttons.find((b) => b.text() === 'Unignore')
    expect(action).toBeDefined()
    await action!.trigger('click')
    expect(wrapper.emitted('toggleIgnore')).toEqual([[1, false]])
  })

  it('applies the ignored class to the root only when the item is ignored', () => {
    const ignored = mount(LibraryCard, { props: { item: { ...baseItem, ignored: true } } })
    expect(ignored.find('.library-item').classes()).toContain('ignored')

    const notIgnored = mount(LibraryCard, { props: { item: { ...baseItem, ignored: false } } })
    expect(notIgnored.find('.library-item').classes()).not.toContain('ignored')
  })

  it('renders no action buttons when the item has no db_id', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, db_id: null } } })
    expect(wrapper.find('.library-item-actions').exists()).toBe(false)
  })

  it('renders the title but no author element when the author is absent', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, author: null } } })
    expect(wrapper.find('.item-author').exists()).toBe(false)
    expect(wrapper.find('h3').text()).toBe('Test Book')
  })

  it('renders a content-type-aware status label for non-book items', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, content_type: 'movie', status: 'unread' } },
    })
    expect(wrapper.find('.badge-status').text()).toBe('Unwatched')
  })

  it('falls back to the unknown status class for an out-of-allowlist status', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, status: 'archived' } } })
    expect(wrapper.find('.badge-status').classes()).toContain('unknown')
  })

  it('applies the currently_consuming status class and label for an in-progress item', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, status: 'currently_consuming' } },
    })
    expect(wrapper.find('.badge-status').classes()).toContain('currently_consuming')
    expect(wrapper.find('.badge-status').text()).toBe('In Progress')
  })

  it('applies the completed status class for a completed item', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, status: 'completed' } },
    })
    expect(wrapper.find('.badge-status').classes()).toContain('completed')
  })
})
