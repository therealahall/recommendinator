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
  it('shows the "Not enriched" badge when the item is not enriched', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, enriched: false } } })
    const badge = wrapper.find('.badge-enrichment')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('Not enriched')
  })

  it('omits the enrichment badge when the item is enriched', () => {
    const wrapper = mount(LibraryCard, { props: { item: { ...baseItem, enriched: true } } })
    expect(wrapper.find('.badge-enrichment').exists()).toBe(false)
  })
})
