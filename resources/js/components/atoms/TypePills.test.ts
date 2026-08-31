import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TypePills from './TypePills.vue'

describe('TypePills', () => {
  it('renders all options including All by default', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('hides All pill when includeAll is false', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book', includeAll: false },
    })

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['Book', 'Movie', 'TV Show', 'Game'])
  })

  it('marks the active pill', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'movie' },
    })

    const pills = wrapper.findAll('.pill')
    const moviePill = pills.find(p => p.text() === 'Movie')!
    expect(moviePill.classes()).toContain('active')

    const bookPill = pills.find(p => p.text() === 'Book')!
    expect(bookPill.classes()).not.toContain('active')
  })

  it('marks All pill as active when modelValue is empty string', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const allPill = wrapper.findAll('.pill').find(p => p.text() === 'All')!
    expect(allPill.classes()).toContain('active')
  })

  it('emits update:modelValue on click', async () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const bookPill = wrapper.findAll('.pill').find(p => p.text() === 'Book')!
    await bookPill.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['book']])
  })

  it('uses radiogroup and radio roles for accessibility', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book' },
    })

    expect(wrapper.find('[role="radiogroup"]').exists()).toBe(true)
    const radios = wrapper.findAll('[role="radio"]')
    expect(radios.length).toBe(5)

    const bookRadio = radios.find(r => r.text() === 'Book')!
    expect(bookRadio.attributes('aria-checked')).toBe('true')

    const movieRadio = radios.find(r => r.text() === 'Movie')!
    expect(movieRadio.attributes('aria-checked')).toBe('false')
  })

  it('has default aria-label on radiogroup', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    expect(wrapper.find('[role="radiogroup"]').attributes('aria-label')).toBe('Content type')
  })

  it('accepts custom aria-label', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '', ariaLabel: 'Enrichment type' },
    })

    expect(wrapper.find('[role="radiogroup"]').attributes('aria-label')).toBe('Enrichment type')
  })

  it('keeps only the checked pill in the tab order', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'movie' },
    })

    const tabbable = wrapper.findAll('.pill').filter(p => p.attributes('tabindex') === '0')
    expect(tabbable.map(p => p.text())).toEqual(['Movie'])
  })

  it('puts the first pill in the tab order when nothing is checked', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '', includeAll: false },
    })

    const tabbable = wrapper.findAll('.pill').filter(p => p.attributes('tabindex') === '0')
    expect(tabbable.map(p => p.text())).toEqual(['Book'])
  })

  it.each([
    ['ArrowRight', 'movie', 'tv_show', 'TV Show'],
    ['ArrowDown', 'movie', 'tv_show', 'TV Show'],
    ['ArrowLeft', 'movie', 'book', 'Book'],
    ['ArrowUp', 'movie', 'book', 'Book'],
    ['Home', 'movie', '', 'All'],
    ['End', 'movie', 'video_game', 'Game'],
    ['ArrowLeft', '', 'video_game', 'Game'],
    ['ArrowRight', 'video_game', '', 'All'],
  ])('%s on the %s pill selects and focuses %s', async (key, checked, emitted, label) => {
    const wrapper = mount(TypePills, {
      props: { modelValue: checked },
      attachTo: document.body,
    })

    const start = wrapper.findAll('.pill').find(p => p.attributes('aria-checked') === 'true')!
    ;(start.element as HTMLElement).focus()
    start.element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:modelValue')).toEqual([[emitted]])
    expect(document.activeElement?.textContent).toBe(label)
    wrapper.unmount()
  })

  it('End reaches the last option of the shortened group when All is hidden', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book', includeAll: false },
    })

    const start = wrapper.findAll('.pill')[0]
    start.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))

    expect(wrapper.emitted('update:modelValue')).toEqual([['video_game']])
  })

  it.each([
    ['altKey', 'ArrowLeft'],
    ['metaKey', 'ArrowLeft'],
    ['ctrlKey', 'Home'],
  ])('leaves %s+%s to the browser shortcut', (modifier, key) => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'movie' },
    })

    const start = wrapper.findAll('.pill').find(p => p.attributes('aria-checked') === 'true')!
    const event = new KeyboardEvent('keydown', {
      key,
      bubbles: true,
      cancelable: true,
      [modifier]: true,
    })
    start.element.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it.each(['Enter', ' '])('leaves %s to the button so Tab then activate still selects', key => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'movie' },
      attachTo: document.body,
    })

    const pill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
    pill.element.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(false)
    wrapper.unmount()
  })
})
