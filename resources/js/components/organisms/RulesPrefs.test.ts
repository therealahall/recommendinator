import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RulesPrefs from './RulesPrefs.vue'
import { usePreferencesStore } from '@/stores/preferences'

describe('RulesPrefs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders a single h3 titled "Rules"', () => {
    const wrapper = mount(RulesPrefs)

    const headings = wrapper.findAll('h3')
    expect(headings).toHaveLength(1)
    expect(headings[0].text()).toBe('Rules')
  })

  it('renders the series-order toggle directly under the Rules heading', () => {
    const wrapper = mount(RulesPrefs)

    const checkbox = wrapper.find('#prefSeriesOrder')
    expect(checkbox.exists()).toBe(true)
    expect(checkbox.attributes('type')).toBe('checkbox')

    const label = wrapper.find('label[for="prefSeriesOrder"]')
    expect(label.text()).toBe('Recommend series in order')

    // The toggle is intro content for the section: it precedes the first h4.
    const firstH4 = wrapper.find('h4')
    const position = checkbox.element.compareDocumentPosition(firstH4.element)
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('groups Length then Custom rules as h4 sub-blocks', () => {
    const wrapper = mount(RulesPrefs)

    const subHeadings = wrapper.findAll('h4').map((h) => h.text())
    expect(subHeadings).toEqual(['Length', 'Custom rules'])
  })

  it('renders the Length dropdowns and Custom rules form', () => {
    const wrapper = mount(RulesPrefs)

    expect(wrapper.findAll('.length-select')).toHaveLength(4)
    expect(wrapper.find('#new-rule-input').exists()).toBe(true)
  })

  it('does not render the old "Length Preferences", "Custom Rules", or "Toggles" headings', () => {
    const wrapper = mount(RulesPrefs)

    expect(wrapper.text()).not.toContain('Length Preferences')
    expect(wrapper.text()).not.toContain('Custom Rules')
    expect(wrapper.text()).not.toContain('Toggles')
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
