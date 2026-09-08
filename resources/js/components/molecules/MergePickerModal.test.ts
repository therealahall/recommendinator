import { describe, it, expect } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MergePickerModal from './MergePickerModal.vue'
import type { ContentItemResponse } from '@/types/api'

function item(db_id: number, title: string): ContentItemResponse {
  return {
    external_ids: [{ source: 'trakt', external_id: String(db_id), display_name: 'Trakt' }],
    db_id,
    title,
    author: 'Jean-Pierre Jeunet',
    content_type: 'movie',
    status: 'unread',
    rating: null,
    review: null,
    source: 'trakt',
    cover_url: null,
    ignored: false,
    seasons_watched: null,
    total_seasons: null,
    release_year: 2001,
    series: null,
    series_index: null,
    enriched: true,
    genres: [],
    tags: [],
    description: null,
  }
}

const anchor = item(1, 'Amelie')
const french = item(2, 'Le Fabuleux Destin d’Amelie Poulain')

function picker(props: Partial<InstanceType<typeof MergePickerModal>['$props']> = {}) {
  return mount(MergePickerModal, {
    attachTo: document.body,
    props: {
      anchor,
      query: 'amelie',
      candidates: [french],
      searching: false,
      merging: false,
      mergeError: '',
      ...props,
    },
  })
}

function button(wrapper: ReturnType<typeof picker>, text: string) {
  const found = wrapper.findAll('button').find((one) => one.text().includes(text))
  expect(found, `no button matching ${text}`).toBeDefined()
  return found!
}

describe('MergePickerModal', () => {
  it('emits the survivor before the absorbed id, whichever side is kept', async () => {
    for (const [keep, expected] of [
      ['Keep “Amelie”', [1, 2]],
      ['Keep “Le Fabuleux', [2, 1]],
    ] as const) {
      const wrapper = picker()
      await button(wrapper, 'Choose “Le Fabuleux').trigger('click')
      await button(wrapper, keep).trigger('click')
      await button(wrapper, 'Merge').trigger('click')

      expect(wrapper.emitted('merge')).toEqual([expected])
      wrapper.unmount()
    }
  })

  it('names the survivor and the absorbed item in the confirmation', async () => {
    const wrapper = picker()

    await button(wrapper, 'Choose “Le Fabuleux').trigger('click')
    await button(wrapper, 'Keep “Amelie”').trigger('click')

    const panel = wrapper.get('[data-testid="confirm-panel"]')
    expect(panel.text()).toContain('Keep “Amelie”')
    expect(panel.text()).toContain('absorb “Le Fabuleux Destin d’Amelie Poulain”')
    wrapper.unmount()
  })

  it('follows the keyboard onto the pair, whose step unmounted the button pressed', async () => {
    const wrapper = picker()
    // The dialog takes focus on mount; without settling that first it lands on
    // the surface after this line and the rescue reads as unnecessary.
    await flushPromises()
    const pick = button(wrapper, 'Choose “Le Fabuleux')
    ;(pick.element as HTMLElement).focus()

    await pick.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(button(wrapper, 'Keep “Amelie”').element)
    wrapper.unmount()
  })

  it('sends the typed term out to be searched rather than filtering in place', async () => {
    const wrapper = picker({ query: '', candidates: [] })

    await wrapper.get('#merge-picker-search').setValue('poulain')

    expect(wrapper.emitted('search')).toEqual([['poulain']])
    wrapper.unmount()
  })

  it('holds the pair still while a merge is in flight, and announces what refused it', async () => {
    const wrapper = picker({ merging: true })
    await button(wrapper, 'Choose “Le Fabuleux').trigger('click')

    await button(wrapper, 'Keep “Amelie”').trigger('click')

    expect(wrapper.find('[data-testid="confirm-panel"]').exists()).toBe(false)
    await wrapper.setProps({ mergeError: 'A book cannot absorb a movie.' })
    expect(wrapper.get('[role="alert"]').text()).toBe('A book cannot absorb a movie.')
    wrapper.unmount()
  })
})
