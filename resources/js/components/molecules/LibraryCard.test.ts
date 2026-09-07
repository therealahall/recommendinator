import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryCard from './LibraryCard.vue'
import RecCard from './RecCard.vue'
import type { ExternalId } from '@/types/api'

const baseItem = {
  external_ids: [
    { source: 'goodreads', external_id: 'test-1', display_name: 'Goodreads' },
  ],
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

  it.each<[string, ExternalId[], string[]]>([
    [
      'one source',
      [{ source: 'gog_work', external_id: 'x', display_name: 'GOG Work' }],
      ['GOG Work'],
    ],
    [
      'a merge of two',
      [
        { source: 'gog_work', external_id: 'x', display_name: 'GOG Work' },
        { source: 'steam', external_id: '440', display_name: 'Steam' },
      ],
      ['GOG Work', 'Steam'],
    ],
    [
      'two ids from one source',
      [
        { source: 'steam', external_id: '440', display_name: 'Steam' },
        { source: 'steam', external_id: '620', display_name: 'Steam' },
      ],
      ['Steam'],
    ],
    [
      'two sources whose names collide',
      [
        { source: 'gog_api', external_id: 'x', display_name: 'GOG API' },
        { source: 'gog-api', external_id: 'y', display_name: 'GOG API' },
      ],
      ['GOG API', 'GOG API'],
    ],
    ['nothing', [], []],
  ])(
    'names each contributing source once by display name, keyed on its id: %s',
    (_case, external_ids, shown) => {
      const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, external_ids } } })

      const badges = wrapper.findAll('[data-testid="source-badge"]')
      expect(badges.map((badge) => badge.text())).toEqual(
        shown.map((name) => expect.stringContaining(name)),
      )
    },
  )

  it('speaks each source badge with context, never a bare id', () => {
    const wrapper = mount(LibraryCard, {
      props: {
        item: {
          ...baseItem,
          external_ids: [{ source: 'steam', external_id: '440', display_name: 'Steam' }],
        },
      },
    })

    const badge = wrapper.get('[data-testid="source-badge"]')
    expect(badge.get('.sr-only').text()).not.toBe('')
  })

  it('gains the source a merge added when the row is refreshed under it', async () => {
    const item = {
      ...baseItem,
      external_ids: [{ source: 'steam', external_id: '440', display_name: 'Steam' }],
    }
    const wrapper = mount(LibraryCard, { props: { item } })

    const merged = [
      ...item.external_ids,
      { source: 'gog_work', external_id: 'x', display_name: 'GOG Work' },
    ]
    await wrapper.setProps({ item: { ...item, external_ids: merged } })

    expect(wrapper.findAll('[data-testid="source-badge"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('GOG Work')
  })

  it('titles a card at the level a recommendation of the same work is titled', () => {
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
