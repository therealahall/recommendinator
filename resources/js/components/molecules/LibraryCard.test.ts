import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryCard from './LibraryCard.vue'
import RecCard from './RecCard.vue'

const baseItem = {
  external_ids: [{ source: 'goodreads', external_id: 'test-1' }],
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
  release_year: null,
  series: null,
  series_index: null,
  enriched: true,
  genres: [],
  tags: [],
  description: null,
}

describe('LibraryCard', () => {
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

  it.each([false, true])('names the item in every action, ignored: %s', (ignored) => {
    // Fifty cards offered a hundred buttons with two names between them (2.4.6).
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, ignored } } })

    const labels = wrapper
      .findAll('.library-item-actions button')
      .map((button) => button.attributes('aria-label') ?? '')

    expect(labels).toHaveLength(2)
    expect(labels.filter((label) => label.includes(baseItem.title))).toEqual(labels)
  })

  it('renders no action buttons when the item has no db_id', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, db_id: null } } })
    expect(wrapper.find('.library-item-actions').exists()).toBe(false)
  })

  it('shows the cover the payload carries, and the missing state when it carries none', () => {
    const withArt = mount(LibraryCard, {
      props: { item: { ...baseItem, cover_url: '/api/covers/1' } },
    })
    const without = mount(LibraryCard, { props: { item: { ...baseItem, cover_url: null } } })

    expect(withArt.get('.cover-art img').attributes('src')).toBe('/api/covers/1')
    expect(without.find('img').exists()).toBe(false)
  })

  it('renders a content-type-aware status label for non-book items', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, content_type: 'movie', status: 'unread' } },
    })
    expect(wrapper.text()).toContain('Unwatched')
  })

  it('titles a card at the level a recommendation of the same work is titled', () => {
    // A page of twenty recommendations offered one heading to jump between: the
    // rec title was a div while the same work here was a heading (WCAG 1.3.1).
    const library = mount(LibraryCard, { props: { item: baseItem } })
    const rec = mount(RecCard, {
      props: {
        rank: 1,
        rec: {
          db_id: 1,
          title: baseItem.title,
          author: baseItem.author,
          content_type: baseItem.content_type,
          cover_url: null,
          series: null,
          series_index: null,
          score: 0.5,
          reasoning: '',
          score_breakdown: {},
          scorer_weights: {},
          variety_penalty: 0,
          contributing_items: [],
          adaptations: [],
        },
      },
    })

    const heading = library.get('h1, h2, h3, h4, h5, h6').element
    expect(heading.textContent).toContain(baseItem.title)
    expect(rec.get('.rec-title').element.tagName).toBe(heading.tagName)
  })

  it.each<[number | null, string]>([
    [1, 'The Murderbot Diaries #1'],
    [null, 'The Murderbot Diaries'],
  ])('names the series the title no longer carries, position %s', (position, shown) => {
    const wrapper = mount(LibraryCard, {
      props: {
        item: { ...baseItem, series: 'The Murderbot Diaries', series_index: position },
      },
    })
    expect(wrapper.find('.item-series').text()).toContain(shown)
  })
})
