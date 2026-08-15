import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import Accordion from './Accordion.vue'

describe('Accordion', () => {
  it('emits update:expanded toggling true when collapsed', async () => {
    const wrapper = mount(Accordion, {
      props: { id: 'src-x', expanded: false },
      slots: { header: 'h', default: 'b' },
    })

    await wrapper.find('button.accordion-trigger').trigger('click')
    expect(wrapper.emitted('update:expanded')).toEqual([[true]])
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
