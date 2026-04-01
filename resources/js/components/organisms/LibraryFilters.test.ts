import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryFilters from './LibraryFilters.vue'

describe('LibraryFilters', () => {
  const defaultProps = {
    typeFilter: '',
    statusFilter: '',
    showIgnored: false,
  }

  it('renders TypePills with all options', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('marks active type pill', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'movie' },
    })

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    expect(moviePill.classes()).toContain('active')
  })

  it('emits filterChange for type on pill click', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const bookPill = wrapper.findAll('.pill').find(p => p.text() === 'Book')!
    await bookPill.trigger('click')

    expect(wrapper.emitted('filterChange')).toEqual([['type', 'book']])
  })

  it('emits filterChange for status on select change', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('select[aria-label="Status"]')
    await select.setValue('completed')

    expect(wrapper.emitted('filterChange')).toEqual([['status', 'completed']])
  })

  it('renders Unwatched label for movies', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'movie' },
    })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Unwatched')
  })

  it('renders Unread label for books', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'book' },
    })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Unread')
  })

  it('renders Unwatched label for tv shows', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'tv_show' },
    })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Unwatched')
  })

  it('renders Unplayed label for video games', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, typeFilter: 'video_game' },
    })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Unplayed')
  })

  it('renders default Not Started label when no type selected', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const options = wrapper.find('select[aria-label="Status"]').findAll('option')
    const unreadOption = options.find(o => o.attributes('value') === 'unread')!
    expect(unreadOption.text()).toBe('Not Started')
  })

  it('emits filterChange for showIgnored on toggle click', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    await wrapper.find('.toggle-switch').trigger('click')

    expect(wrapper.emitted('filterChange')).toEqual([['showIgnored', true]])
  })

  it('emits filterChange with false when toggling off showIgnored', async () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, showIgnored: true },
    })

    await wrapper.find('.toggle-switch').trigger('click')

    expect(wrapper.emitted('filterChange')).toEqual([['showIgnored', false]])
  })

  it('renders export dropdown button with title', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    expect(exportBtn).toBeDefined()
    expect(exportBtn.attributes('title')).toBe('Export library items')
  })

  it('export button is always enabled regardless of type filter', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    expect(exportBtn.attributes('disabled')).toBeUndefined()
  })

  it('shows dropdown menu on export click', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')

    expect(wrapper.find('.dropdown-menu').exists()).toBe(true)
    const menuButtons = wrapper.find('.dropdown-menu').findAll('button')
    expect(menuButtons.map(b => b.text())).toEqual(['CSV', 'JSON'])
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

  it('emits export with json and closes menu', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')

    const jsonBtn = wrapper.find('.dropdown-menu').findAll('button').find(b => b.text() === 'JSON')!
    await jsonBtn.trigger('click')

    expect(wrapper.emitted('export')).toEqual([['json']])
    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
  })

  it('closes dropdown on Escape key', async () => {
    const wrapper = mount(LibraryFilters, {
      props: defaultProps,
      attachTo: document.body,
    })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')
    expect(wrapper.find('.dropdown-menu').exists()).toBe(true)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
    wrapper.unmount()
  })

  it('closes dropdown on click outside', async () => {
    const wrapper = mount(LibraryFilters, {
      props: defaultProps,
      attachTo: document.body,
    })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')
    expect(wrapper.find('.dropdown-menu').exists()).toBe(true)

    document.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
    wrapper.unmount()
  })

  it('closes dropdown on second Export button click', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')
    expect(wrapper.find('.dropdown-menu').exists()).toBe(true)

    await exportBtn.trigger('click')
    expect(wrapper.find('.dropdown-menu').exists()).toBe(false)
  })

  it('renders toolbar dividers', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const dividers = wrapper.findAll('.toolbar-divider')
    expect(dividers.length).toBe(2)
  })

  it('renders TypeSelect for mobile with all options', () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('.lib-type-select')
    expect(select.exists()).toBe(true)

    const options = select.findAll('option')
    expect(options.map(o => o.text().trim())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('emits filterChange for type from TypeSelect', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('.lib-type-select')
    const el = select.element as HTMLSelectElement
    el.value = 'movie'
    await select.trigger('change')

    expect(wrapper.emitted('filterChange')).toEqual([['type', 'movie']])
  })

  it('reflects typeFilter prop in TypeSelect value', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    await wrapper.setProps({ typeFilter: 'book' })

    const el = wrapper.find('.lib-type-select').element as HTMLSelectElement
    expect(el.value).toBe('book')
  })

  it('export button has aria-expanded', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    expect(exportBtn.attributes('aria-expanded')).toBe('false')

    await exportBtn.trigger('click')
    expect(exportBtn.attributes('aria-expanded')).toBe('true')
  })

  it('dropdown menu contains export buttons without ARIA menu roles', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const exportBtn = wrapper.findAll('.btn').find(b => b.text() === 'Export')!
    await exportBtn.trigger('click')

    const menu = wrapper.find('.dropdown-menu')
    expect(menu.attributes('role')).toBeUndefined()

    const items = menu.findAll('button')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toBe('CSV')
    expect(items[1].text()).toBe('JSON')
  })
})
