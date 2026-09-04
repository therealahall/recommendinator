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
  series: null,
  series_index: null,
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

  it('Escape closes a dialog nobody has edited', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it.each([
    ['Escape', async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['the backdrop', async (w: ReturnType<typeof mount>) => w.trigger('click')],
    [
      'Cancel',
      async (w: ReturnType<typeof mount>) =>
        w.findAll('button').find((b) => b.text() === 'Cancel')!.trigger('click'),
    ],
  ])('%s asks before discarding a typed review, and declining keeps it', async (_name, dismiss) => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await wrapper.find('#edit-review').setValue('A long review.')

    await dismiss(wrapper)
    await vi.runAllTimersAsync()

    expect(wrapper.emitted('close')).toBeFalsy()
    const asked = wrapper.get('.confirm-panel')
    expect(wrapper.get('[aria-modal="true"]').element.contains(asked.element)).toBe(true)

    await asked.findAll('button').find(b => b.text() === 'Keep editing')!.trigger('click')

    expect(wrapper.emitted('close')).toBeFalsy()
    expect((wrapper.get('#edit-review').element as HTMLTextAreaElement).value).toBe(
      'A long review.',
    )
    expect(
      wrapper.get('[aria-modal="true"]').element.contains(document.activeElement),
    ).toBe(true)
    wrapper.unmount()
  })

  it('a second Escape dismisses the confirmation and puts the caret back in the review', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    const review = wrapper.get('#edit-review')
    await review.setValue('A long review.')
    ;(review.element as HTMLTextAreaElement).focus()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await vi.runAllTimersAsync()
    expect(document.activeElement).toBe(wrapper.get('.confirm-panel').element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await vi.runAllTimersAsync()

    expect(wrapper.find('.confirm-panel').exists()).toBe(false)
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(document.activeElement).toBe(review.element)
    wrapper.unmount()
  })

  it('discarding from the confirmation closes the dialog', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await wrapper.get('[aria-label="4 stars"]').trigger('click')
    await wrapper.trigger('click')
    await vi.runAllTimersAsync()

    await wrapper.get('.confirm-panel').findAll('button')
      .find(b => b.text() === 'Discard')!.trigger('click')

    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('offers a manually enriched item its way back to automatic enrichment', async () => {
    const wrapper = mount(EditModal, {
      props: {
        item: { ...defaultItem, enriched: true, manually_enriched: true },
        saving: false,
        saveError: '',
      },
      attachTo: document.body,
    })

    await wrapper.findAll('button')
      .find(b => b.text().includes('Restore automatic enrichment'))!.trigger('click')

    expect(wrapper.emitted('restoreEnrichment')).toEqual([[1]])
    wrapper.unmount()
  })

  it('a restored item says so and keeps focus in the dialog, not on the button that vanished', async () => {
    const manual = { ...defaultItem, enriched: true, manually_enriched: true }
    const wrapper = mount(EditModal, {
      props: { item: manual, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    const said = wrapper.get('[role="status"]')
    const whileManual = said.text()
    const restore = wrapper.findAll('button')
      .find(b => b.text().includes('Restore automatic enrichment'))!
    ;(restore.element as HTMLElement).focus()
    await restore.trigger('click')

    await wrapper.setProps({ item: { ...manual, enriched: true, manually_enriched: false } })
    await vi.runAllTimersAsync()

    expect(said.text()).not.toBe(whileManual)
    expect(said.text()).not.toBe('')
    expect(document.activeElement).toBe(said.element)
    const dialog = wrapper.get('[aria-modal="true"]').element
    expect(dialog.contains(document.activeElement)).toBe(true)
    const tabbable = [...dialog.querySelectorAll<HTMLElement>('button, input, select, textarea')]
    expect(
      tabbable.some(
        (el) => said.element.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
      ),
    ).toBe(true)
    wrapper.unmount()
  })

  it('says what editing the enrichment fields costs', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })

    expect(wrapper.text()).toContain('opts the item out of automatic enrichment')
    expect(wrapper.text()).not.toContain('Restore automatic enrichment')
    wrapper.unmount()
  })

  it('a book offers no year box, and save emits only the fields that changed', async () => {
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
      tags: ['classic'],
      description: 'A tale.',
    })
    wrapper.unmount()
  })

  it('a rating alone sends the rating alone', async () => {
    const item = { ...defaultItem, genres: ['Sci-Fi'], tags: ['classic'], description: 'A tale.' }
    const wrapper = mount(EditModal, {
      props: { item, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.get('[aria-label="4 stars"]').trigger('click')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ rating: 4 })
    wrapper.unmount()
  })

  it('an emptied description is sent as the clear, not dropped', async () => {
    const wrapper = mount(EditModal, {
      props: {
        item: { ...defaultItem, description: 'A tale.' },
        saving: false,
        saveError: '',
      },
      attachTo: document.body,
    })

    await wrapper.find('#edit-description').setValue('   ')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ description: '' })
    wrapper.unmount()
  })

  it('emptying the genre chips clears the list', async () => {
    const wrapper = mount(EditModal, {
      props: {
        item: { ...defaultItem, genres: ['Sci-Fi'] },
        saving: false,
        saveError: '',
      },
      attachTo: document.body,
    })

    await wrapper.get('[aria-label="Remove Sci-Fi"]').trigger('click')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ genres: [] })
    wrapper.unmount()
  })

  it('save serializes a whitespace-only review to null', async () => {
    const wrapper = mount(EditModal, {
      props: { item: { ...defaultItem, review: 'Loved it.' }, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.find('#edit-review').setValue('   ')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ review: null })
    wrapper.unmount()
  })

  it('picking completed ticks every season and sends both', async () => {
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.find('#edit-status').setValue('completed')

    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    expect(wrapper.emitted('save')![0][1]).toEqual({
      status: 'completed',
      seasons_watched: [1, 2, 3, 4, 5],
    })
    wrapper.unmount()
  })

  it('Reset to synced sends the clear the CLI flag sends, not a list of rejections', async () => {
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false, saveError: '' },
      attachTo: document.body,
    })

    await wrapper.findAll('button').find((one) => one.text() === 'Reset to synced')!.trigger('click')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ status: 'unread', clear_seasons: true })
    wrapper.unmount()
  })

  it('opened as Mark complete it preselects completed, and the choice stays editable', async () => {
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false, saveError: '', initialStatus: 'completed' },
      attachTo: document.body,
    })

    expect((wrapper.get('#edit-status').element as HTMLSelectElement).value).toBe('completed')

    await wrapper.findAll('.season-checkbox')[4].trigger('click')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')

    expect(wrapper.emitted('save')![0][1]).toEqual({ seasons_watched: [1, 2, 3, 4] })
    wrapper.unmount()
  })

  it('opened from the library it keeps the item\'s own status', async () => {
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false, saveError: '' },
      attachTo: document.body,
    })

    expect((wrapper.get('#edit-status').element as HTMLSelectElement).value).toBe(
      'currently_consuming',
    )
    wrapper.unmount()
  })

  it('save emits a corrected release year, and a creator trimmed to exactly the bound', async () => {
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

  it('says a refused save inside the dialog, without taking the keyboard off Save', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    expect(complaints(wrapper.findAll('[role="alert"]'))).toEqual([])
    await vi.runAllTimersAsync()
    const save = wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!
    ;(save.element as HTMLElement).focus()

    await wrapper.setProps({ saveError: 'Review must be at most 10000 characters.' })
    await vi.runAllTimersAsync()

    const said = wrapper.get('[role="alert"]')
    expect(said.text()).toBe('Review must be at most 10000 characters.')
    expect(wrapper.get('[aria-modal="true"]').element.contains(said.element)).toBe(true)
    expect(document.activeElement).toBe(save.element)
    wrapper.unmount()
  })

  it('keeps Save under the focus that pressed it, and sends one request for two presses', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await wrapper.get('[aria-label="4 stars"]').trigger('click')
    const save = wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!
    ;(save.element as HTMLElement).focus()

    await save.trigger('click')
    await wrapper.setProps({ saving: true })

    expect(save.element.matches('[disabled]')).toBe(false)
    expect(document.activeElement).toBe(save.element)
    await save.trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each([
    ['Review must be at most 10000 characters.', '#edit-review'],
    ['Creator cannot be empty.', '#edit-creator'],
    ['Release year must be a number between 1800 and 2100.', '#edit-release-year'],
  ])('the refusal "%s" is attached to the box it is about', async (saveError, selector) => {
    const wrapper = mount(EditModal, {
      props: {
        item: { ...defaultItem, content_type: 'movie' },
        saving: false,
        saveError: '',
      },
      attachTo: document.body,
    })
    expect(wrapper.get(selector).attributes('aria-invalid')).toBeUndefined()

    await wrapper.setProps({ saveError })

    const field = wrapper.get(selector)
    expect(field.attributes('aria-invalid')).toBe('true')
    expect(wrapper.get(`#${field.attributes('aria-describedby')}`).text()).toBe(saveError)
    expect(wrapper.findAll('[aria-invalid="true"]')).toHaveLength(1)
    wrapper.unmount()
  })

})
