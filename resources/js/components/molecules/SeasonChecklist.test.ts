import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SeasonChecklist from './SeasonChecklist.vue'

describe('SeasonChecklist watched marker', () => {
  it('marks watched seasons with a glyph, so the state does not rest on colour alone', () => {
    const wrapper = mount(SeasonChecklist, {
      props: { totalSeasons: 4, modelValue: [2, 3] },
    })

    const marked = wrapper
      .findAll('.season-checkbox')
      .filter((label) => label.find('svg').exists())
      .map((label) => label.text())

    expect(marked.sort()).toEqual(['2', '3'])
  })

  it('keeps a watched season past the total, which the grid never rendered to untick', async () => {
    const wrapper = mount(SeasonChecklist, {
      props: { totalSeasons: 2, modelValue: [1, 4] },
    })
    const button = (label: string) =>
      wrapper.findAll('button').find((one) => one.text() === label)!

    await button('Select All').trigger('click')
    await button('Deselect All').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([[[1, 2, 4]], [[4]]])
  })

  it('marks a season the moment the toggled value comes back from the parent', async () => {
    const wrapper = mount(SeasonChecklist, {
      props: { totalSeasons: 10, modelValue: [] },
    })
    const seasonTwo = () => wrapper.findAll('.season-checkbox').find((one) => one.text() === '2')!

    await seasonTwo().find('input').trigger('change')

    expect(wrapper.emitted('update:modelValue')![0]).toEqual([[2]])
    await wrapper.setProps({ modelValue: [2] })
    expect(seasonTwo().find('svg').exists()).toBe(true)
  })
})
