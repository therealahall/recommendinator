import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryFilters from './LibraryFilters.vue'
import { MAX_SEARCH_LENGTH } from '@/constants/library'
import { componentStyles } from '@/testing/styles'

describe('LibraryFilters', () => {
  const defaultProps = {
    typeFilter: '',
    statusFilter: '',
    enrichmentFilter: '',
    showIgnored: false,
    needsRating: false,
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

  it('disables and displays Completed without mutating an empty statusFilter', () => {
    const wrapper = mount(LibraryFilters, {
      props: { ...defaultProps, needsRating: true, statusFilter: '' },
    })

    const select = wrapper.find('select[aria-label="Status"]')
    expect(select.attributes('disabled')).toBeDefined()
    // Display-only: the select shows Completed even though statusFilter is ''.
    expect((select.element as HTMLSelectElement).value).toBe('completed')
    expect(select.attributes('aria-describedby')).toBe('status-locked-hint')
    expect(wrapper.find('#status-locked-hint').classes()).toContain('sr-only')
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

  it('emits filterChange for type from TypeSelect', async () => {
    const wrapper = mount(LibraryFilters, { props: defaultProps })

    const select = wrapper.find('.lib-type-select')
    const el = select.element as HTMLSelectElement
    el.value = 'movie'
    await select.trigger('change')

    expect(wrapper.emitted('filterChange')).toEqual([['type', 'movie']])
  })
})

describe('LibraryFilters mobile overflow regression (issue #102)', () => {
  /**
   * Bug: at 375px the filter controls overflowed the card and widened the page.
   * Root cause: the rows are flex with no wrap and a select cannot shrink past
   * its widest option. Fix: wrap them, set a basis.
   */
  function mobileBlock(): string {
    const styles = componentStyles('resources/js/components/organisms/LibraryFilters.vue')
    const match = styles.match(/@media \(max-width: 640px\) \{([\s\S]*?)\n\}/)
    if (!match) throw new Error('640px block not found in LibraryFilters.vue')
    return match[1]
  }

  it('lets both control rows wrap on mobile', () => {
    expect(mobileBlock()).toMatch(/\.lib-filter-row,\s*\.lib-actions-row\s*\{[^}]*flex-wrap:\s*wrap/)
  })
})
