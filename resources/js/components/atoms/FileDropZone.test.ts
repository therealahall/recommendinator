import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import FileDropZone from './FileDropZone.vue'

function exportFile(): File {
  return new File(['title,author\n'], 'goodreads_library_export.csv', {
    type: 'text/csv',
  })
}

function mountZone(file: File | null = null) {
  return mount(FileDropZone, {
    props: { inputId: 'import-file', label: 'File', file },
  })
}

describe('FileDropZone', () => {
  it('gives the native file input a label pointing at it', () => {
    const wrapper = mountZone()

    const input = wrapper.get('input[type="file"]')
    expect(input.attributes('id')).toBe('import-file')
    expect(wrapper.get('label').attributes('for')).toBe('import-file')
    wrapper.unmount()
  })

  it('emits the file a change on the input carried', async () => {
    const wrapper = mountZone()
    const chosen = exportFile()

    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [chosen],
      configurable: true,
    })
    await input.trigger('change')

    expect(wrapper.emitted('update:file')).toEqual([[chosen, false]])
    wrapper.unmount()
  })

  it('emits the dropped file, so a drop and a keyboard pick agree', async () => {
    const wrapper = mountZone()
    const dropped = exportFile()

    await wrapper
      .get('.drop-zone')
      .trigger('drop', { dataTransfer: { files: [dropped] } })

    expect(wrapper.emitted('update:file')).toEqual([[dropped, true]])
    wrapper.unmount()
  })

  it('emits nothing for a drop carrying no file, leaving the choice standing', async () => {
    const wrapper = mountZone(exportFile())

    await wrapper.get('.drop-zone').trigger('drop', { dataTransfer: { files: [] } })

    expect(wrapper.emitted('update:file')).toBeUndefined()
    wrapper.unmount()
  })

  it('names the chosen file in the description the input points at', () => {
    const wrapper = mountZone(exportFile())

    const selection = wrapper.get('[data-testid="drop-zone-selection"]')
    expect(selection.text()).toBe('Selected file: goodreads_library_export.csv')
    expect(wrapper.get('input[type="file"]').attributes('aria-describedby')).toContain(
      selection.attributes('id'),
    )
    wrapper.unmount()
  })

  it('keeps the drag highlight up while the pointer crosses a child element', async () => {
    const wrapper = mountZone()
    const zone = wrapper.get('.drop-zone')

    await zone.trigger('dragenter')
    await zone.trigger('dragenter')
    await zone.trigger('dragleave')

    expect(zone.classes()).toContain('drop-zone-over')
    await zone.trigger('dragleave')
    expect(zone.classes()).not.toContain('drop-zone-over')
    wrapper.unmount()
  })
})
