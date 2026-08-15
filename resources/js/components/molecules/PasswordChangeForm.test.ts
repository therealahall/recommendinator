import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import PasswordChangeForm from './PasswordChangeForm.vue'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'

// Long enough to clear the length rule, so a test about anything else is not
// answered by it.
const LONG = 'hunter3-hunter3'
const OTHER = 'hunter4-hunter4'

async function fillIn(
  wrapper: VueWrapper,
  values: { current?: string; replacement?: string; confirmation?: string },
): Promise<void> {
  if (values.current !== undefined) {
    await wrapper.find('#account-current-password').setValue(values.current)
  }
  if (values.replacement !== undefined) {
    await wrapper.find('#account-new-password').setValue(values.replacement)
  }
  if (values.confirmation !== undefined) {
    await wrapper.find('#account-confirm-password').setValue(values.confirmation)
  }
}

describe('PasswordChangeForm', () => {
  it('emits the current password alongside the new one', async () => {
    const wrapper = mount(PasswordChangeForm)

    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ current_password: 'hunter2', new_password: LONG }],
    ])
  })

  it('states and enforces the floor it was handed, not one of its own', async () => {
    // Regression: the floor was compiled into the bundle, so a server enforcing
    // another one had this form inviting a password it would then refuse.
    const server = PASSWORD_MIN_LENGTH + 4
    const between = 'x'.repeat(server - 1)
    const wrapper = mount(PasswordChangeForm, { props: { minPasswordLength: server } })

    expect(wrapper.find('#account-new-password-hint').text()).toContain(String(server))

    await fillIn(wrapper, { current: 'hunter2', replacement: between, confirmation: between })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#account-password-status').text()).toContain(String(server))
  })

  it('refuses a mismatch locally and marks the two fields that disagree', async () => {
    const wrapper = mount(PasswordChangeForm)

    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: OTHER })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#account-password-status').text()).toContain('do not match')
    expect(wrapper.find('#account-new-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#account-confirm-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#account-current-password').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#account-current-password').attributes('aria-describedby')).toBeUndefined()
  })

  it('keeps all three drafts after a refusal', async () => {
    const wrapper = mount(PasswordChangeForm)
    await fillIn(wrapper, { current: 'wrong', replacement: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    await wrapper.setProps({ error: 'That is not your current password.' })

    expect(wrapper.find<HTMLInputElement>('#account-current-password').element.value).toBe('wrong')
    expect(wrapper.find<HTMLInputElement>('#account-new-password').element.value).toBe(LONG)
  })

  it('empties the fields only once the change is confirmed', async () => {
    const wrapper = mount(PasswordChangeForm)
    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: LONG })

    await wrapper.setProps({ saved: true })

    expect(wrapper.find<HTMLInputElement>('#account-current-password').element.value).toBe('')
    expect(wrapper.find<HTMLInputElement>('#account-new-password').element.value).toBe('')
    expect(wrapper.find<HTMLInputElement>('#account-confirm-password').element.value).toBe('')
    expect(wrapper.find('#account-password-status').text()).toBe('Password changed.')
  })
})
