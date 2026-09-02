import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RulesPrefs from './RulesPrefs.vue'
import { usePreferencesStore } from '@/stores/preferences'

describe('RulesPrefs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('gives the section exactly one h3, so it is one landmark in the outline', () => {
    const wrapper = mount(RulesPrefs)

    expect(wrapper.findAll('h3')).toHaveLength(1)
  })

  it('heads each sub-block one level under the section, so the outline skips none', () => {
    const wrapper = mount(RulesPrefs)
    const levels = new Set(
      [...wrapper.element.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => h.tagName),
    )

    // 1.3.1. Rename or reorder the sub-blocks freely; a demoted one skips h4.
    expect(levels).toEqual(new Set(['H3', 'H4']))
  })

  it('gives the series-order checkbox a label with words in it', () => {
    const wrapper = mount(RulesPrefs)

    const checkbox = wrapper.find('#prefSeriesOrder')
    expect(checkbox.exists()).toBe(true)
    expect(checkbox.attributes('type')).toBe('checkbox')

    // Reword it freely; an empty label leaves the toggle unnamed to a reader.
    expect(wrapper.find('label[for="prefSeriesOrder"]').text()).not.toBe('')
  })

  it('renders the Length dropdowns and Custom rules form', () => {
    const wrapper = mount(RulesPrefs)

    expect(wrapper.findAll('select[id^="length-pref-"]')).toHaveLength(4)
    expect(wrapper.find('#new-rule-input').exists()).toBe(true)
  })

  it('flips seriesInOrder when the series-order checkbox is toggled', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(RulesPrefs)

    expect(prefs.seriesInOrder).toBe(true)
    await wrapper.find('#prefSeriesOrder').setValue(false)
    expect(prefs.seriesInOrder).toBe(false)
  })

  it('updates contentLengthPreferences when a Length dropdown changes', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(RulesPrefs)

    const bookSelect = wrapper.find('#length-pref-book')
    await bookSelect.setValue('short')

    expect(prefs.contentLengthPreferences.book).toBe('short')
  })

  it('renders custom rules as <li> items in a list with labelled remove buttons', () => {
    const prefs = usePreferencesStore()
    prefs.addRule('avoid horror')
    prefs.addRule('prefer sci-fi')
    const wrapper = mount(RulesPrefs)

    // role="list" reasserts list semantics WebKit/VoiceOver strips when
    // list-style: none is applied.
    const list = wrapper.find('ul.rule-list')
    expect(list.attributes('role')).toBe('list')

    const items = wrapper.findAll('ul.rule-list[role="list"] > li.rule-item')
    expect(items).toHaveLength(2)

    const removeButtons = wrapper.findAll('.rule-item button')
    expect(removeButtons.map((b) => b.attributes('aria-label'))).toEqual([
      'Remove rule: avoid horror',
      'Remove rule: prefer sci-fi',
    ])
  })

  it('adds then removes a custom rule through the form', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(RulesPrefs)

    const input = wrapper.find('#new-rule-input')
    await input.setValue('avoid horror')
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(prefs.customRules).toContain('avoid horror')

    await wrapper.find('.rule-item button').trigger('click')
    expect(prefs.customRules).not.toContain('avoid horror')
  })
})
