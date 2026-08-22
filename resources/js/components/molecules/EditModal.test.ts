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
  ])('%s asks before discarding a typed review, and declining keeps it', async (_name, dismiss) => {
    // Both gestures used to close on the spot, taking a long review with them.
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await wrapper.find('#edit-review').setValue('A long review.')

    await dismiss(wrapper)
    await vi.runAllTimersAsync()

    expect(wrapper.emitted('close')).toBeFalsy()
    const asked = wrapper.get('[role="alertdialog"]')
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

  it('discarding from the confirmation closes the dialog', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false, saveError: '' },
      attachTo: document.body,
    })
    await wrapper.get('[aria-label="4 stars"]').trigger('click')
    await wrapper.trigger('click')
    await vi.runAllTimersAsync()

    await wrapper.get('[role="alertdialog"]').findAll('button')
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
    // Regression: genres, tags and description went with every save, and the
    // door stamps any of them "manual" — a rating dropped the item out of the
    // Not enriched filter and out of automatic enrichment for good.
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
    // Regression: the payload used `description || null`, and null is the
    // door's "leave alone", so an emptied box silently kept the old text.
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
    // Regression: '   ' went as a string, and a stored blank review reads as
    // one the user wrote, blocking every later import from filling the field.
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
      seasons_watched: [1, 2, 3, 4, 5],
    })
    wrapper.unmount()
  })

  it('opened as Mark complete it preselects completed, and the choice stays editable', async () => {
    // Seeded from a recommendation's own status the shared dialog opened on
    // "unread", so a rating saved from it marked nothing complete.
    const wrapper = mount(EditModal, {
      props: { item: tvItem, saving: false, saveError: '', initialStatus: 'completed' },
      attachTo: document.body,
    })

    expect((wrapper.get('#edit-status').element as HTMLSelectElement).value).toBe('completed')
    expect(wrapper.findAll('.season-checkbox.checked')).toHaveLength(5)

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
    // Refusing the bound itself leaves a 600-character import nothing to trim to.
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
