import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AuthField from './AuthField.vue'

function mountField(props: Record<string, unknown> = {}) {
  return mount(AuthField, {
    props: { id: 'field', label: 'Username', modelValue: '', autocomplete: 'username', ...props },
  })
}

describe('AuthField', () => {
  it('names its input with a real label, not a placeholder', () => {
    const wrapper = mountField()

    expect(wrapper.find('label').attributes('for')).toBe('field')
    expect(wrapper.find('label').text()).toBe('Username')
    expect(wrapper.find('input').attributes('id')).toBe('field')
    expect(wrapper.find('input').attributes('placeholder')).toBeUndefined()
  })

  it('describes the input by its hint and the form status together', () => {
    const wrapper = mountField({ hint: 'What you type to sign in.', describedBy: 'form-status' })

    expect(wrapper.find('input').attributes('aria-describedby')).toBe('field-hint form-status')
    expect(wrapper.find('#field-hint').text()).toBe('What you type to sign in.')
  })

  it('emits each edit rather than mutating the bound value', async () => {
    const wrapper = mountField()

    await wrapper.find('input').setValue('aaron')

    expect(wrapper.emitted('update:modelValue')).toEqual([['aaron']])
  })
})
