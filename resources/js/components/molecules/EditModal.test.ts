import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
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

  it('a book offers no year box, and save emits its status, genres, tags and description', async () => {
    const item = { ...defaultItem, genres: ['Sci-Fi'], tags: [], description: null }
    const wrapper = mount(EditModal, {
      props: { item, saving: false },
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
      props: { item: { ...defaultItem, review: 'Loved it.' }, saving: false },
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
      props: { item, saving: false },
      attachTo: document.body,
    })

    await wrapper.find('#edit-release-year').setValue('1993')
    await wrapper.find('#edit-creator').setValue(atTheBound)
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    const payload = wrapper.emitted('save')![0][1] as ItemEditRequest
    expect(payload.release_year).toBe(1993)
    expect(payload.creator).toBe(atTheBound)
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('refuses a corrected creator the API would, and clears the complaint as it is fixed', async () => {
    // maxlength bounds what is typed, never the 600-character author an import wrote.
    const refused = [
      { author: 'Author', typed: '', says: 'Creator cannot be empty' },
      {
        author: 'a'.repeat(600),
        typed: 'b'.repeat(MAX_CREATOR_LENGTH + 20),
        says: String(MAX_CREATOR_LENGTH),
      },
    ]

    for (const { author, typed, says } of refused) {
      const wrapper = mount(EditModal, {
        props: { item: { ...defaultItem, author }, saving: false },
        attachTo: document.body,
      })

      await wrapper.find('#edit-creator').setValue(typed)
      await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

      expect(wrapper.emitted('save')).toBeUndefined()
      const field = wrapper.find('#edit-creator')
      expect(field.attributes('aria-invalid')).toBe('true')
      const complaint = wrapper.get(`#${field.attributes('aria-describedby')}`)
      expect(complaint.text()).toContain(says)
      expect(complaint.attributes('role')).toBe('alert')
      expect(document.activeElement).toBe(field.element)

      await field.setValue('Someone Else')

      expect(wrapper.find('#edit-creator').attributes('aria-invalid')).toBeUndefined()
      expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
      wrapper.unmount()
    }
  })

  it('an emptied, out-of-range or non-numeric year is refused, not discarded', async () => {
    const item = { ...defaultItem, content_type: 'video_game', release_year: 2016 }

    for (const typed of ['', '20', '2016 (remaster)']) {
      const wrapper = mount(EditModal, {
        props: { item, saving: false },
        attachTo: document.body,
      })

      await wrapper.find('#edit-release-year').setValue(typed)
      await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

      expect(wrapper.emitted('save')).toBeUndefined()
      const field = wrapper.find('#edit-release-year')
      expect((field.element as HTMLInputElement).value).toBe(typed)
      expect(field.attributes('aria-invalid')).toBe('true')
      const complaint = wrapper.get(`#${field.attributes('aria-describedby')}`)
      expect(complaint.text()).toContain('Enter a year between 1800 and 2200')
      expect(complaint.attributes('role')).toBe('alert')
      expect(document.activeElement).toBe(field.element)
      wrapper.unmount()
    }
  })

  it('rates an item whose stored year or creator the API would refuse, or states neither', async () => {
    const stored = [
      { content_type: 'movie', release_year: 19993 },
      { author: 'a'.repeat(600) },
      { content_type: 'movie', author: null, release_year: null },
    ]

    for (const fields of stored) {
      const wrapper = mount(EditModal, {
        props: { item: { ...defaultItem, ...fields }, saving: false },
        attachTo: document.body,
      })

      await wrapper.get('[aria-label="4 stars"]').trigger('click')
      await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

      const payload = wrapper.emitted('save')![0][1] as ItemEditRequest
      expect(payload.rating).toBe(4)
      expect(payload).not.toHaveProperty('creator')
      expect(payload).not.toHaveProperty('release_year')
      expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
      wrapper.unmount()
    }
  })

  it('clears a refused year as it is retyped, not at the next save press', async () => {
    // The alert outlived the correction: "1994, invalid entry" was announced.
    const item = { ...defaultItem, content_type: 'video_game', release_year: 2016 }
    const wrapper = mount(EditModal, {
      props: { item, saving: false },
      attachTo: document.body,
    })

    await wrapper.find('#edit-release-year').setValue('20')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    expect(wrapper.find('#edit-release-year').attributes('aria-invalid')).toBe('true')

    await wrapper.find('#edit-release-year').setValue('19')
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)

    await wrapper.find('#edit-release-year').setValue('1994')

    expect(wrapper.find('#edit-release-year').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(0)
    wrapper.unmount()
  })

})
