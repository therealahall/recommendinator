import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ItemCover from './ItemCover.vue'

describe('ItemCover', () => {
  it('draws the missing-art state from a null cover_url, without requesting anything', () => {
    const wrapper = mount(ItemCover, {
      props: { coverUrl: null, contentType: 'movie', title: 'Dune' },
    })

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.sr-only').text()).toBe('No cover art for Dune')
  })

  it('falls back to the same state when a cached cover 404s', async () => {
    const wrapper = mount(ItemCover, {
      props: { coverUrl: '/api/covers/7', contentType: 'book', title: 'Piranesi' },
    })
    expect(wrapper.get('img').attributes('src')).toBe('/api/covers/7')

    await wrapper.get('img').trigger('error')

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.sr-only').text()).toBe('No cover art for Piranesi')
  })

  it('retries the next item rather than staying failed on a recycled box', async () => {
    const wrapper = mount(ItemCover, {
      props: { coverUrl: '/api/covers/7', contentType: 'book', title: 'Piranesi' },
    })
    await wrapper.get('img').trigger('error')

    await wrapper.setProps({ coverUrl: '/api/covers/8', title: 'Babel' })

    expect(wrapper.get('img').attributes('src')).toBe('/api/covers/8')
  })

  it('leaves the art out of the accessibility tree, which the row already names', () => {
    const wrapper = mount(ItemCover, {
      props: { coverUrl: '/api/covers/7', contentType: 'book', title: 'Piranesi' },
    })

    expect(wrapper.get('img').attributes('alt')).toBe('')
  })
})
