import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, type DOMWrapper } from '@vue/test-utils'
import EditModal from './EditModal.vue'
import { MAX_CREATOR_LENGTH } from '@/constants/library'
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

function complaints(regions: DOMWrapper<Element>[]): string[] {
  return regions.map((one) => one.text()).filter(Boolean)
}

describe('EditModal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('Escape key emits close', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('a book offers no year box, and save emits its status, genres, tags and description', async () => {
    const item = { ...defaultItem, genres: ['Sci-Fi'], tags: [], description: null }
    const wrapper = mount(EditModal, {
      props: { item, saving: false, saveError: '' },
      attachTo: document.body,
    })
    expect(wrapper.find('#edit-release-year').exists()).toBe(false)
    await wrapper.find('#edit-status').setValue('completed')
    await wrapper.find('#edit-tags').setValue('classic')
    await wrapper.find('#edit-tags').trigger('keypress', { key: 'Enter' })
    await wrapper.find('#edit-description').setValue('A tale.')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    const emitted = wrapper.emitted('save')!
    expect(emitted[0][0]).toBe(1)
    expect(emitted[0][1]).toEqual({
      status: 'completed',
      rating: null,
      review: null,
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
      props: { item: { ...defaultItem, review: 'Loved it.' }, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.find('#edit-review').setValue('   ')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({
      status: 'unread',
      rating: null,
      review: null,
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
      props: { item: tvItem, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.find('#edit-status').setValue('completed')

    expect(wrapper.findAll('.season-checkbox.checked')).toHaveLength(5)
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    expect(wrapper.emitted('save')![0][1]).toEqual({
      status: 'completed',
      rating: null,
      review: null,
      genres: [],
      tags: [],
      description: null,
      seasons_watched: [1, 2, 3, 4, 5],
    })
    wrapper.unmount()
  })

  it('save emits a corrected release year, and a creator trimmed to exactly the bound', async () => {
    // The bound itself is what `library edit --creator` and the API both take,
    // so refusing it would leave a 600-character import with nothing to trim to.
    const atTheBound = 'id Software '.padEnd(MAX_CREATOR_LENGTH, 'x')
    const item = {
      ...defaultItem,
      content_type: 'video_game',
      release_year: 2016,
      author: 'a'.repeat(600),
    }
    const wrapper = mount(EditModal, {
      props: { item, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.find('#edit-release-year').setValue('1993')
    await wrapper.find('#edit-creator').setValue(atTheBound)
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    const payload = wrapper.emitted('save')![0][1] as ItemEditRequest
    expect(payload.release_year).toBe('1993')
    expect(payload.creator).toBe(atTheBound)
    expect(complaints(wrapper.findAll('[role="alert"]'))).toEqual([])
    wrapper.unmount()
  })

  it('an emptied creator or mistyped year is sent for the API to refuse, not blocked here', async () => {
    // Regression: an unparseable year serialized to NaN, which JSON writes as
    // null — the save then quietly left the stored year as it was. A cleared
    // creator was blocked before the request, so only the dialog ever said no.
    const item = { ...defaultItem, content_type: 'video_game', release_year: 2016 }

    for (const typed of ['', '20', '2016 (remaster)']) {
      const wrapper = mount(EditModal, {
        props: { item, saving: false, saveError: '' },
        attachTo: document.body,
      })

      await wrapper.find('#edit-release-year').setValue(typed)
      await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

      expect((wrapper.emitted('save')![0][1] as ItemEditRequest).release_year).toBe(typed)
      wrapper.unmount()
    }

    const cleared = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await cleared.find('#edit-creator').setValue('')
    await cleared.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect((cleared.emitted('save')![0][1] as ItemEditRequest).creator).toBe('')
    cleared.unmount()
  })

  it('rates an item whose stored year or creator the API would refuse, or states neither', async () => {
    const stored = [
      { content_type: 'movie', release_year: 19993 },
      { author: 'a'.repeat(600) },
      { content_type: 'movie', author: null, release_year: null },
    ]

    for (const fields of stored) {
      const wrapper = mount(EditModal, {
        props: { item: { ...defaultItem, ...fields }, saving: false, saveError: '' },
        attachTo: document.body,
      })

      await wrapper.get('[aria-label="4 stars"]').trigger('click')
      await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

      const payload = wrapper.emitted('save')![0][1] as ItemEditRequest
      expect(payload.rating).toBe(4)
      expect(payload).not.toHaveProperty('creator')
      expect(payload).not.toHaveProperty('release_year')
      expect(complaints(wrapper.findAll('[role="alert"]'))).toEqual([])
      wrapper.unmount()
    }
  })

  it('says a refused save inside the dialog, and puts focus on it', async () => {
    // The page banner it used to land on renders behind this overlay and
    // outside the aria-modal subtree, so nobody saving ever saw the reason.
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    expect(complaints(wrapper.findAll('[role="alert"]'))).toEqual([])

    await wrapper.setProps({ saveError: 'Review must be at most 10000 characters.' })
    await vi.runAllTimersAsync()

    const said = wrapper.get('[role="alert"]')
    expect(said.text()).toBe('Review must be at most 10000 characters.')
    expect(wrapper.get('[aria-modal="true"]').element.contains(said.element)).toBe(true)
    expect(document.activeElement).toBe(said.element)
    wrapper.unmount()
  })

})
