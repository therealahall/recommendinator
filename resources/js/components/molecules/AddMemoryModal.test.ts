import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import AddMemoryModal from './AddMemoryModal.vue'

describe('AddMemoryModal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('has role="dialog" and aria-modal', () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('aria-modal')).toBe('true')
    wrapper.unmount()
  })

  it('has aria-labelledby matching the title heading', () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.attributes('aria-labelledby')).toBe('add-memory-title')
    expect(wrapper.find('#add-memory-title').text()).toBe('Add Memory')
    wrapper.unmount()
  })

  it('textarea has accessible label', () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    expect(wrapper.find('label[for="memory-textarea"]').exists()).toBe(true)
    expect(wrapper.find('#memory-textarea').exists()).toBe(true)
    wrapper.unmount()
  })

  it('Escape key emits close', async () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    await vi.runAllTimersAsync()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('backdrop click emits close', async () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    await wrapper.find('.memory-modal').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('save emits trimmed text', async () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    await wrapper.find('#memory-textarea').setValue('  hello world  ')
    await wrapper.findAll('.btn-primary').find(b => b.text() === 'Save')!.trigger('click')
    expect(wrapper.emitted('save')).toEqual([['hello world']])
    wrapper.unmount()
  })

  it('empty text does not emit save', async () => {
    const wrapper = mount(AddMemoryModal, { attachTo: document.body })
    await wrapper.find('#memory-textarea').setValue('   ')
    await wrapper.findAll('.btn-primary').find(b => b.text() === 'Save')!.trigger('click')
    expect(wrapper.emitted('save')).toBeUndefined()
    wrapper.unmount()
  })
})
