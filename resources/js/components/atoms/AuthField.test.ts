import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AuthField from './AuthField.vue'

function mountField(props: Record<string, unknown> = {}, attached = false) {
  return mount(AuthField, {
    props: { id: 'field', label: 'Username', modelValue: '', autocomplete: 'username', ...props },
    ...(attached ? { attachTo: document.body } : {}),
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

  it('masks a password and passes the autocomplete a manager reads', () => {
    const wrapper = mountField({ type: 'password', autocomplete: 'new-password' })

    const input = wrapper.find('input')
    expect(input.attributes('type')).toBe('password')
    expect(input.attributes('autocomplete')).toBe('new-password')
  })

  it('stops a phone capitalising a username into a failed sign-in', () => {
    const wrapper = mountField()

    const input = wrapper.find('input')
    expect(input.attributes('autocapitalize')).toBe('none')
    expect(input.attributes('autocorrect')).toBe('off')
    expect(input.attributes('spellcheck')).toBe('false')
  })

  it('leaves a display name alone, where capitals are wanted', () => {
    const wrapper = mountField({ autocomplete: 'nickname' })

    expect(wrapper.find('input').attributes('autocapitalize')).toBeUndefined()
  })

  it('describes the input by its hint and the form status together', () => {
    const wrapper = mountField({ hint: 'What you type to sign in.', describedBy: 'form-status' })

    expect(wrapper.find('input').attributes('aria-describedby')).toBe('field-hint form-status')
    expect(wrapper.find('#field-hint').text()).toBe('What you type to sign in.')
  })

  it('points at nothing when there is nothing to point at', () => {
    const wrapper = mountField()

    expect(wrapper.find('input').attributes('aria-describedby')).toBeUndefined()
  })

  it('marks the field invalid only when told to', async () => {
    const wrapper = mountField()
    expect(wrapper.find('input').attributes('aria-invalid')).toBeUndefined()

    await wrapper.setProps({ invalid: true })

    expect(wrapper.find('input').attributes('aria-invalid')).toBe('true')
  })

  it('marks the field required both ways, or neither', async () => {
    // aria-required as well as the attribute: the submit button locks with
    // aria-disabled, so nothing else tells a screen reader why it refuses.
    const wrapper = mountField()
    expect(wrapper.find('input').attributes('required')).toBeUndefined()
    expect(wrapper.find('input').attributes('aria-required')).toBeUndefined()

    await wrapper.setProps({ required: true })

    expect(wrapper.find('input').attributes('required')).toBeDefined()
    expect(wrapper.find('input').attributes('aria-required')).toBe('true')
  })

  it('takes focus on mount only where the screen asks for it', () => {
    const quiet = mountField({ id: 'quiet' }, true)
    expect(document.activeElement).not.toBe(quiet.find('input').element)

    const eager = mountField({ id: 'eager', autofocus: true }, true)

    expect(document.activeElement).toBe(eager.find('input').element)
    quiet.unmount()
    eager.unmount()
  })

  it('emits each edit rather than mutating the bound value', async () => {
    const wrapper = mountField()

    await wrapper.find('input').setValue('aaron')

    expect(wrapper.emitted('update:modelValue')).toEqual([['aaron']])
  })
})

describe('AuthField on a phone', () => {
  // jsdom applies no stylesheet, so the scoped block is read off disk.
  function inputRule(): string {
    const source = readFileSync(
      `${process.cwd()}/resources/js/components/atoms/AuthField.vue`,
      'utf8',
    )
    const match = source.match(/\.auth-field input\s*\{([^}]*)\}/)
    if (!match) throw new Error('.auth-field input rule not found in AuthField.vue')
    return match[1]
  }

  it('is a thumb-sized target', () => {
    expect(inputRule()).toMatch(/min-height:\s*44px/)
  })

  it('holds 16px text, under which iOS Safari zooms the form off-screen', () => {
    expect(inputRule()).toMatch(/font-size:\s*1rem/)
  })
})
