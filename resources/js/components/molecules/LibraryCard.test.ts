import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryCard from './LibraryCard.vue'

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
    expect(action!.classes()).toContain('btn-ignore')
    await action!.trigger('click')
    expect(wrapper.emitted('toggleIgnore')).toEqual([[1, true]])
  })

  it('renders no action buttons when the item has no db_id', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, db_id: null } } })
    expect(wrapper.find('.library-item-actions').exists()).toBe(false)
  })

  it('renders a content-type-aware status label for non-book items', () => {
    const wrapper = mount(LibraryCard, {
      props: { item: { ...baseItem, content_type: 'movie', status: 'unread' } },
    })
    expect(wrapper.find('.badge-status').text()).toBe('Unwatched')
  })
})
