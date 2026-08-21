import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import EditModal from './EditModal.vue'
import type { ItemEditRequest } from '@/types/api'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({ get: vi.fn() }),
}))

const defaultItem = {
  external_ids: [{ source: 'goodreads', external_id: 'test-1' }],
  db_id: 1,
  title: 'Test Book',
  content_type: 'book',
  source: 'goodreads',
  status: 'unread',
  rating: null,
  review: null,
  author: 'Author',
  seasons_watched: null,
  total_seasons: null,
  release_year: null,
  ignored: false,
  enriched: false,
  genres: [],
  tags: [],
  description: null,
}

const tvItem = {
  ...defaultItem,
  title: 'Test Show',
  content_type: 'tv_show',
  total_seasons: 5,
  seasons_watched: [1, 2],
  status: 'currently_consuming',
}

describe('EditModal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('Escape key emits close', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('save emits with correct payload', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    await wrapper.find('#edit-status').setValue('completed')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    const emitted = wrapper.emitted('save')!
    expect(emitted[0][0]).toBe(1)
    expect(emitted[0][1]).toEqual({
      status: 'completed',
      rating: null,
      review: null,
      creator: 'Author',
      genres: [],
      tags: [],
      description: null,
    })
    wrapper.unmount()
  })

  it('save emits edited genres, tags, and description', async () => {
    const item = { ...defaultItem, genres: ['Sci-Fi'], tags: [], description: null }
    const wrapper = mount(EditModal, {
      props: { item, saving: false },
      attachTo: document.body,
    })
    await wrapper.find('#edit-tags').setValue('classic')
    await wrapper.find('#edit-tags').trigger('keypress', { key: 'Enter' })
    await wrapper.find('#edit-description').setValue('A tale.')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    const emitted = wrapper.emitted('save')!
    expect(emitted[0][1]).toEqual({
      status: 'unread',
      rating: null,
      review: null,
      creator: 'Author',
      genres: ['Sci-Fi'],
      tags: ['classic'],
      description: 'A tale.',
    })
    wrapper.unmount()
  })

  it('save serializes a whitespace-only review to null', async () => {
    // Regression: the payload coerced with `|| null`, so '' cleared the review
    // but '   ' was sent as a string. A stored blank review reads as one the
    // user wrote and blocks any later import from filling the field, so the
    // API rejects it — the modal now clears on blank, as the CLI does.
    const wrapper = mount(EditModal, {
      props: { item: { ...defaultItem, review: 'Loved it.' }, saving: false },
      attachTo: document.body,
    })

    await wrapper.find('#edit-review').setValue('   ')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({
      status: 'unread',
      rating: null,
      review: null,
      creator: 'Author',
      genres: [],
      tags: [],
      description: null,
    })
    wrapper.unmount()
  })

  it('picking completed ticks every season and sends both', async () => {
    // Regression (#123): the modal resent the half-ticked checklist beside the
    // new status, and the backend derived currently_consuming back over it.
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false },
      attachTo: document.body,
    })

    await wrapper.find('#edit-status').setValue('completed')

    expect(wrapper.findAll('.season-checkbox.checked')).toHaveLength(5)
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    expect(wrapper.emitted('save')![0][1]).toEqual({
      status: 'completed',
      rating: null,
      review: null,
      creator: 'Author',
      genres: [],
      tags: [],
      description: null,
      seasons_watched: [1, 2, 3, 4, 5],
    })
    wrapper.unmount()
  })

  it('save emits a corrected release year and creator', async () => {
    const item = { ...defaultItem, content_type: 'video_game', release_year: 2016 }
    const wrapper = mount(EditModal, {
      props: { item, saving: false },
      attachTo: document.body,
    })

    await wrapper.find('#edit-release-year').setValue('1993')
    await wrapper.find('#edit-creator').setValue('id Software')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    const payload = wrapper.emitted('save')![0][1] as ItemEditRequest
    expect(payload.release_year).toBe(1993)
    expect(payload.creator).toBe('id Software')
    wrapper.unmount()
  })

  it('a book sends no release year, having none to correct', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })

    expect(wrapper.find('#edit-release-year').exists()).toBe(false)
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).not.toHaveProperty('release_year')
    wrapper.unmount()
  })
})
