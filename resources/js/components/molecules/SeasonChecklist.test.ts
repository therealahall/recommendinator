import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SeasonChecklist from './SeasonChecklist.vue'
import { componentStyles } from '@/testing/styles'

describe('SeasonChecklist mobile overflow regression', () => {
  /**
   * Bug: at 320px the control row scrolled sideways. Root cause: it is flex
   * with no wrap, and the counter grew to a sentence that no longer fits
   * beside two nowrap buttons. Fix: wrap the row.
   */
  it('lets the control row wrap', () => {
    const styles = componentStyles('resources/js/components/molecules/SeasonChecklist.vue')
    const rule = styles.match(/\.season-controls\s*\{([^}]*)\}/)
    if (!rule) throw new Error('.season-controls rule not found')

    expect(rule[1]).toMatch(/flex-wrap:\s*wrap/)
  })

  it('keeps the buttons and the counter inside .season-controls, which the rule targets', () => {
    const wrapper = mount(SeasonChecklist, {
      props: { totalSeasons: 5, modelValue: [1, 2, 3] },
    })

    const controls = wrapper.find('.season-controls')
    expect(controls.findAll('button').map(b => b.text())).toEqual(['Select All', 'Deselect All'])
    expect(controls.find('.season-counter').text()).toBe('3 of 5 seasons watched')
  })
})
