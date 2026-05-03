import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import Accordion from './Accordion.vue'

describe('Accordion', () => {
  it('renders header slot inside the trigger button', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: {
        header: '<span class="hdr">Steam</span>',
        default: '<p>Body</p>',
      },
    })

    const button = wrapper.find('button.accordion-trigger')
    expect(button.exists()).toBe(true)
    expect(button.find('.hdr').text()).toBe('Steam')
  })

  it('marks the trigger with aria-expanded=false when collapsed', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: { header: 'h', default: 'b' },
    })

    expect(
      wrapper.find('button.accordion-trigger').attributes('aria-expanded'),
    ).toBe('false')
  })

  it('marks the trigger with aria-expanded=true when expanded', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: true },
      slots: { header: 'h', default: 'b' },
    })

    expect(
      wrapper.find('button.accordion-trigger').attributes('aria-expanded'),
    ).toBe('true')
  })

  it('wires aria-controls on the trigger to the panel id', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: true },
      slots: { header: 'h', default: 'b' },
    })

    const button = wrapper.find('button.accordion-trigger')
    const panel = wrapper.find('[role="region"]')
    expect(panel.exists()).toBe(true)
    expect(button.attributes('aria-controls')).toBe(panel.attributes('id'))
  })

  it('wires aria-labelledby on the panel back to the trigger id', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: true },
      slots: { header: 'h', default: 'b' },
    })

    const button = wrapper.find('button.accordion-trigger')
    const panel = wrapper.find('[role="region"]')
    expect(panel.attributes('aria-labelledby')).toBe(button.attributes('id'))
  })

  it('emits update:expanded toggling true when collapsed', async () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: { header: 'h', default: 'b' },
    })

    await wrapper.find('button.accordion-trigger').trigger('click')
    expect(wrapper.emitted('update:expanded')).toEqual([[true]])
  })

  it('emits update:expanded toggling false when expanded', async () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: true },
      slots: { header: 'h', default: 'b' },
    })

    await wrapper.find('button.accordion-trigger').trigger('click')
    expect(wrapper.emitted('update:expanded')).toEqual([[false]])
  })

  it('hides panel content from assistive tech when collapsed', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: { header: 'h', default: '<p class="b">Body</p>' },
    })

    const panel = wrapper.find('[role="region"]')
    expect(panel.attributes('hidden')).toBe('')
  })

  it('exposes panel content when expanded', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: true },
      slots: { header: 'h', default: '<p class="b">Body</p>' },
    })

    const panel = wrapper.find('[role="region"]')
    expect(panel.attributes('hidden')).toBeUndefined()
    expect(panel.find('.b').text()).toBe('Body')
  })

  it('derives unique trigger and panel ids from the id prop', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'steam_source', expanded: false },
      slots: { header: 'h', default: 'b' },
    })

    const button = wrapper.find('button.accordion-trigger')
    const panel = wrapper.find('[role="region"]')
    expect(button.attributes('id')).toBe('accordion-steam_source-trigger')
    expect(panel.attributes('id')).toBe('accordion-steam_source-panel')
  })

  it('renders header-actions slot as siblings outside the trigger button', () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: {
        header: 'Steam',
        'header-actions': '<button data-testid="action">Sync</button>',
        default: 'b',
      },
    })

    const action = wrapper.find('[data-testid="action"]')
    expect(action.exists()).toBe(true)
    // The action button must NOT be nested inside the accordion trigger button.
    const trigger = wrapper.find('button.accordion-trigger').element
    expect(trigger.contains(action.element)).toBe(false)
  })
})
