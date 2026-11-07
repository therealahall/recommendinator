import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RulesPrefs from './RulesPrefs.vue'
import { usePreferencesStore } from '@/stores/preferences'

describe('RulesPrefs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('heads each sub-block one level under the section, so the outline skips none', () => {
    const wrapper = mount(RulesPrefs)
    const levels = new Set(
      [...wrapper.element.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => h.tagName),
    )

    expect(levels).toEqual(new Set(['H3', 'H4']))
  })

  it('gives the series-order checkbox a label with words in it', () => {
    const wrapper = mount(RulesPrefs)

    const checkbox = wrapper.find('#prefSeriesOrder')
    expect(checkbox.exists()).toBe(true)
    expect(checkbox.attributes('type')).toBe('checkbox')

    expect(wrapper.find('label[for="prefSeriesOrder"]').text()).not.toBe('')
  })

  it('renders the Length dropdowns', () => {
    const wrapper = mount(RulesPrefs)

    expect(wrapper.findAll('select[id^="length-pref-"]')).toHaveLength(4)
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
})
