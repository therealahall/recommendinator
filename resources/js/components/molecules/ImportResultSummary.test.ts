import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ImportResultSummary from './ImportResultSummary.vue'
import type { ImportResponse } from '@/types/api'

/** Worked example 2 of the epic: 240 unchanged with 2 skipped is a success. */
const partlySuccessful: ImportResponse = {
  importer: 'goodreads_csv',
  content_type: 'book',
  filename: 'goodreads_library_export.csv',
  added: 12,
  updated: 3,
  unchanged: 240,
  skipped: 2,
  failed: 0,
  total_rows: 257,
  errors: [
    'Skipped line 14: no title',
    'Skipped line 88: 6 fields short of the header',
  ],
}

describe('ImportResultSummary', () => {
  /** All five outcomes are distinct numbers, and a count rendered nowhere is a
   *  row the operator never learns about. */
  it('renders every one of the five counts and the rows read', () => {
    const wrapper = mount(ImportResultSummary, { props: { result: partlySuccessful } })

    const shown = (key: string) =>
      wrapper.get(`[data-testid="import-count-${key}"]`).text()
    expect(shown('added')).toBe('12')
    expect(shown('updated')).toBe('3')
    expect(shown('unchanged')).toBe('240')
    expect(shown('skipped')).toBe('2')
    expect(shown('failed')).toBe('0')
    expect(shown('total_rows')).toBe('257')
    wrapper.unmount()
  })

  it('lists the reason for every skipped and failed line', () => {
    const wrapper = mount(ImportResultSummary, { props: { result: partlySuccessful } })

    const lines = wrapper.get('[data-testid="import-errors"]').findAll('li')
    expect(lines.map((line) => line.text())).toEqual([
      'Skipped line 14: no title',
      'Skipped line 88: 6 fields short of the header',
    ])
    wrapper.unmount()
  })

  /** A partly successful import is the normal case. Announcing it as an alert
   *  would interrupt the operator to report a success. */
  it('reports a partly successful import without an alert role', () => {
    const wrapper = mount(ImportResultSummary, { props: { result: partlySuccessful } })

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="import-errors"]').text()).toContain('line 14')
    wrapper.unmount()
  })

  it('drops the misses block when every line imported', () => {
    const wrapper = mount(ImportResultSummary, {
      props: {
        result: { ...partlySuccessful, skipped: 0, total_rows: 255, errors: [] },
      },
    })

    expect(wrapper.find('[data-testid="import-errors"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
