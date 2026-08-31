import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryFilters from './LibraryFilters.vue'
import { DEFAULT_SORT, MAX_SEARCH_LENGTH, SORT_OPTIONS } from '@/constants/library'

describe('LibraryFilters', () => {
  const defaultProps = {
    typeFilter: '',
    statusFilter: '',
    enrichmentFilter: '',
    showIgnored: false,
    needsRating: false,
    sortBy: DEFAULT_SORT,
    searchQuery: '',
    searchLoading: false,
  }

  it('bounds the search input by the length the API accepts', () => {
    // Regression: the input had no maxlength, so a term over MAX_SEARCH_LENGTH
    // reached ?search and came back 422 instead of an empty result set.
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const input = wrapper.find('.lib-search input')
    expect(input.attributes('maxlength')).toBe(String(MAX_SEARCH_LENGTH))
  })

  it('emits filterChange for search on input', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    await wrapper.find('.lib-search input').setValue('dune')

    expect(wrapper.emitted('filterChange')).toEqual([['search', 'dune']])
  })

  it('emits filterChange for status on select change', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('select[aria-label="Status"]')
    await select.setValue('completed')

    expect(wrapper.emitted('filterChange')).toEqual([['status', 'completed']])
  })

  it('renders Unplayed label for video games', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'video_game' },
    })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Unplayed')
  })

  it('withdraws the Status filter Needs rating overrides, and says so on screen', async () => {
    // Offered but ignoring what it was given, the select explained the refusal
    // to a screen reader alone; the sighted operator saw the value snap back.
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, needsRating: true, statusFilter: '' },
    })

    expect(wrapper.find('select[aria-label="Status"]').exists()).toBe(false)
    const note = wrapper.get('.lib-filter-row .help-text')
    expect(note.classes()).not.toContain('sr-only')
    expect(note.text()).toContain('Completed')

    await wrapper.setProps({ needsRating: false })

    expect(wrapper.find('select[aria-label="Status"]').exists()).toBe(true)
  })

  it('emits export with csv and closes menu', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')

    const csvBtn = wrapper.find('.dropdown-menu').findAll('button').find(b => b.text() === 'CSV')!
    await csvBtn.trigger('click')

    expect(wrapper.emitted('export')).toEqual([['csv']])
    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
  })

  it.each([
    ['a format is chosen', (menu: HTMLElement) => menu.querySelector('button')!.click()],
    ['Escape is pressed', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['the click lands outside', () => document.body.click()],
  ])('gives the keyboard back to Export when %s', async (_name, close) => {
    const wrapper = mount(LibraryFilters, { props: defaultProps, attachTo: document.body })
    const trigger = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await trigger.trigger('click')
    const menu = wrapper.get('.dropdown-menu')
    expect(document.getElementById(trigger.attributes('aria-controls')!)).toBe(menu.element)
    ;(menu.findAll('button')[0].element as HTMLElement).focus()

    close(menu.element as HTMLElement)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('leaves the keyboard in the box the closing click moved it to', async () => {
    // An unconditional trigger.focus() here reads the same on every path above,
    // and sends the next keystroke of a search to the Export button.
    const wrapper = mount(LibraryFilters, { props: defaultProps, attachTo: document.body })
    await wrapper.findAll('.btn').find(b => b.text() === 'Export')!.trigger('click')
    const search = wrapper.get('.lib-search input')

    ;(search.element as HTMLElement).focus()
    await search.trigger('click')

    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
    expect(document.activeElement).toBe(search.element)
    wrapper.unmount()
  })

  it('offers every sort order the API accepts and emits the choice', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('select[aria-label="Sort"]')
    const values = select.findAll('option').map(o => o.attributes('value'))
    await select.setValue('rating')

    expect(values.sort()).toEqual(SORT_OPTIONS.map(o => o.value).slice().sort())
    expect(wrapper.emitted('filterChange')).toEqual([['sort', 'rating']])
  })

  it('reads the scope note out at each format button', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    await wrapper.findAll('.btn').find(b => b.text() === 'Export')!.trigger('click')

    const described = wrapper.findAll('.dropdown-menu-item').map((button) => {
      const id = button.attributes('aria-describedby')
      return id ? wrapper.find(`#${id}`).text() : ''
    })
    expect(described).toEqual([
      expect.stringContaining('whole library'),
      expect.stringContaining('whole library'),
    ])
  })

  it('emits filterChange for type from TypeSelect', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('.lib-type-select')
    const el = select.element as HTMLSelectElement
    el.value = 'movie'
    await select.trigger('change')

    expect(wrapper.emitted('filterChange')).toEqual([['type', 'movie']])
  })
})
