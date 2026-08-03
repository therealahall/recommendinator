import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SearchInput from './SearchInput.vue'

describe('SearchInput', () => {
  it('renders a search input with role=search wrapper', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    expect(wrapper.find('[role="search"]').exists()).toBe(true)
    expect(wrapper.find('input[type="search"]').exists()).toBe(true)
  })

  it('wires the label to the input via default id', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    const label = wrapper.find('label.sr-only')
    const input = wrapper.find('input')
    expect(label.text()).toBe('Search library')
    expect(label.attributes('for')).toBe('library-search')
    expect(input.attributes('id')).toBe('library-search')
  })

  it('accepts custom id, label and placeholder', () => {
    const wrapper = mount(SearchInput, {
      props: { modelValue: '', id: 'my-search', label: 'Find stuff', placeholder: 'Type here' },
    })
    expect(wrapper.find('label').attributes('for')).toBe('my-search')
    expect(wrapper.find('label').text()).toBe('Find stuff')
    expect(wrapper.find('input').attributes('id')).toBe('my-search')
    expect(wrapper.find('input').attributes('placeholder')).toBe('Type here')
  })

  it('reflects modelValue on the input', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'hello' } })
    const input = wrapper.find('input').element as HTMLInputElement
    expect(input.value).toBe('hello')
  })

  it('emits update:modelValue on input', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    const input = wrapper.find('input')
    await input.setValue('dune')
    expect(wrapper.emitted('update:modelValue')).toEqual([['dune']])
  })

  it('decorative search icon is aria-hidden', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    const svgs = wrapper.findAll('svg')
    expect(svgs.length).toBeGreaterThan(0)
    expect(svgs[0].attributes('aria-hidden')).toBe('true')
  })

  it('hides the clear button when there is no text', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    expect(wrapper.find('.search-input-clear').exists()).toBe(false)
  })

  it('shows the clear button only when text is present', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
    const clear = wrapper.find('.search-input-clear')
    expect(clear.exists()).toBe(true)
    expect(clear.attributes('aria-label')).toBe('Clear search')
  })

  it('clear button emits empty modelValue then clear', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
    await wrapper.find('.search-input-clear').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
    expect(wrapper.emitted('clear')).toEqual([[]])
  })

  it('clear button restores focus to the input', async () => {
    const wrapper = mount(SearchInput, {
      props: { modelValue: 'abc' },
      attachTo: document.body,
    })
    await wrapper.find('.search-input-clear').trigger('click')
    expect(document.activeElement).toBe(wrapper.find('input').element)
    wrapper.unmount()
  })

  it('spinner is aria-hidden while loading', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc', loading: true } })
    expect(wrapper.find('.spinner').attributes('aria-hidden')).toBe('true')
  })

  it('Escape clears when text is present and prevents default', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
    expect(wrapper.emitted('clear')).toEqual([[]])
  })

  it('Escape does nothing when there is no text', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.emitted('clear')).toBeUndefined()
  })

  it('Escape clears even while loading', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc', loading: true } })
    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
    expect(wrapper.emitted('clear')).toEqual([[]])
  })

  it('shows a spinner and hides the clear button while loading', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc', loading: true } })
    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(wrapper.find('.search-input-clear').exists()).toBe(false)
  })

  it('sets aria-busy from the loading prop', () => {
    const idle = mount(SearchInput, { props: { modelValue: '' } })
    expect(idle.find('input').attributes('aria-busy')).toBe('false')
    const busy = mount(SearchInput, { props: { modelValue: 'x', loading: true } })
    expect(busy.find('input').attributes('aria-busy')).toBe('true')
  })

  it('disables native autocomplete', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    expect(wrapper.find('input').attributes('autocomplete')).toBe('off')
  })

  describe('character limit', () => {
    // Regression: the input carried no bound, so a term longer than the one the
    // API accepts came back 422 — a bare status line over an emptied list where
    // the user used to get the ordinary "no items match" empty state.
    //
    // maxlength alone stops typing silently, and truncates a paste with no
    // feedback at all, so reaching the cap also renders a visible notice and
    // fills a live region.
    //
    // Regression: that live region was rendered with v-if, so it entered the
    // DOM already holding its text. Screen readers register live regions as
    // they join the accessibility tree and commonly skip one that arrives
    // populated (JAWS notably), so hitting the cap announced nothing at all
    // (WCAG 4.1.3). The region is now persistent and only its text changes.
    it('leaves the input unbounded when no limit is given', () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
      expect(wrapper.find('input').attributes('maxlength')).toBeUndefined()
      expect(wrapper.find('.search-input-limit').exists()).toBe(false)
    })

    it('bounds the input and stays quiet below the limit', () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abcd', maxlength: 5 } })
      expect(wrapper.find('input').attributes('maxlength')).toBe('5')
      expect(wrapper.find('.search-input-limit').exists()).toBe(false)
      expect(wrapper.find('input').attributes('aria-describedby')).toBeUndefined()
    })

    it('keeps the live region mounted and empty before the limit is reached', () => {
      const unbounded = mount(SearchInput, { props: { modelValue: 'abc' } })
      const idle = unbounded.find('[role="status"]')
      expect(idle.exists()).toBe(true)
      expect(idle.text()).toBe('')

      const bounded = mount(SearchInput, { props: { modelValue: 'abcd', maxlength: 5 } })
      const below = bounded.find('[role="status"]')
      expect(below.exists()).toBe(true)
      expect(below.text()).toBe('')
    })

    it('fills the already-mounted live region when the limit is reached', async () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abcd', maxlength: 5 } })
      const region = wrapper.find('[role="status"]')
      expect(region.text()).toBe('')

      await wrapper.setProps({ modelValue: 'abcde' })

      expect(wrapper.find('[role="status"]').element).toBe(region.element)
      expect(region.text()).toBe(
        'Search is limited to 5 characters. Anything longer is not included.',
      )
      expect(region.classes()).toContain('sr-only')
      expect(region.attributes('aria-live')).toBe('polite')
      expect(region.attributes('aria-atomic')).toBe('true')
    })

    it('describes the input with a visible notice that does not announce twice', () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abcde', maxlength: 5 } })
      const notice = wrapper.find('.search-input-limit')
      expect(notice.text()).toBe(
        'Search is limited to 5 characters. Anything longer is not included.',
      )
      expect(notice.attributes('role')).toBeUndefined()
      expect(notice.attributes('aria-live')).toBeUndefined()
      expect(notice.attributes('id')).toBe('library-search-limit')
      expect(wrapper.find('input').attributes('aria-describedby')).toBe('library-search-limit')
    })

    it('scopes the notice id to a custom input id', () => {
      const wrapper = mount(SearchInput, {
        props: { modelValue: 'abcde', maxlength: 5, id: 'my-search' },
      })
      expect(wrapper.find('.search-input-limit').attributes('id')).toBe('my-search-limit')
      expect(wrapper.find('input').attributes('aria-describedby')).toBe('my-search-limit')
    })
  })
})
