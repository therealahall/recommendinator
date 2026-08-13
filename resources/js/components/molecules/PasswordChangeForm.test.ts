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
  it('is long enough to be submittable, so the tests below are not all refusals', () => {
    expect(LONG.length).toBeGreaterThanOrEqual(PASSWORD_MIN_LENGTH)
    expect(OTHER.length).toBeGreaterThanOrEqual(PASSWORD_MIN_LENGTH)
    expect(LONG).not.toBe(OTHER)
  })

  it('emits the current password alongside the new one', async () => {
    const wrapper = mount(PasswordChangeForm)

    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ current_password: 'hunter2', new_password: LONG }],
    ])
  })

  it('masks all three fields and tells a manager which is which', () => {
    const wrapper = mount(PasswordChangeForm)

    for (const id of ['#account-current-password', '#account-new-password', '#account-confirm-password']) {
      expect(wrapper.find(id).attributes('type')).toBe('password')
      expect(wrapper.find(id).attributes('required'), id).toBeDefined()
      expect(wrapper.find(id).attributes('aria-required'), id).toBe('true')
    }
    expect(wrapper.find('#account-current-password').attributes('autocomplete')).toBe(
      'current-password',
    )
    expect(wrapper.find('#account-new-password').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.find('#account-confirm-password').attributes('autocomplete')).toBe(
      'new-password',
    )
  })

  it('states the length rule before it can be broken', async () => {
    // Regression: the rule was left to the server, which refuses a short
    // password with a 422 whose detail is a list — rendered as no rule at all.
    const wrapper = mount(PasswordChangeForm)

    expect(wrapper.find('#account-new-password-hint').text()).toContain(
      String(PASSWORD_MIN_LENGTH),
    )

    await fillIn(wrapper, { current: 'hunter2', replacement: 'short', confirmation: 'short' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#account-password-status').text()).toContain(String(PASSWORD_MIN_LENGTH))
    expect(wrapper.find('#account-new-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#account-current-password').attributes('aria-invalid')).toBeUndefined()
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

  it('marks no field invalid for a refusal it cannot attribute to one', async () => {
    // Regression: aria-invalid sat on the current-password field, so a server
    // refusal about the new one pointed at the one field that was right.
    const wrapper = mount(PasswordChangeForm)

    await wrapper.setProps({ error: 'That password could not be changed.' })

    for (const id of ['#account-current-password', '#account-new-password', '#account-confirm-password']) {
      expect(wrapper.find(id).attributes('aria-invalid'), id).toBeUndefined()
      // The message is in the region and in every field's description instead.
      expect(wrapper.find(id).attributes('aria-describedby'), id).toContain(
        'account-password-status',
      )
    }
  })

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mount(PasswordChangeForm)

    const region = wrapper.find('#account-password-status')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('transitions one region through a refusal without remounting it', async () => {
    // A live region that first enters the tree already populated is read as page
    // content and skipped (WCAG 4.1.3), so the node has to outlive the refusal.
    const wrapper = mount(PasswordChangeForm)
    const region = wrapper.find('#account-password-status').element

    await wrapper.setProps({ pending: true })
    expect(wrapper.find('#account-password-status').text()).toBe('Changing your password…')

    await wrapper.setProps({ pending: false, error: 'That is not your current password.' })

    expect(wrapper.find('#account-password-status').element).toBe(region)
    expect(wrapper.find('#account-password-status').text()).toContain('current password')
    expect(wrapper.find('#account-current-password').attributes('aria-describedby')).toBe(
      'account-password-status',
    )
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

  it('locks the submit button until all three fields are filled', async () => {
    const wrapper = mount(PasswordChangeForm)
    const button = () => wrapper.find('[data-testid="account-password-save"]')
    expect(button().attributes('aria-disabled')).toBe('true')

    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: LONG })

    expect(button().attributes('aria-disabled')).toBeUndefined()
  })

  it('never locks the button natively, so an accepted change cannot blur it', async () => {
    // Regression: the save emptied all three fields, the button went native
    // `disabled`, and focus fell from it to <body> — past the whole sidebar on
    // the Settings page (WCAG 2.4.3).
    const wrapper = mount(PasswordChangeForm)
    const button = () => wrapper.find('[data-testid="account-password-save"]')
    expect(button().attributes('disabled')).toBeUndefined()

    await fillIn(wrapper, { current: 'hunter2', replacement: LONG, confirmation: LONG })
    await wrapper.setProps({ saved: true })

    expect(button().attributes('aria-disabled')).toBe('true')
    expect(button().attributes('disabled')).toBeUndefined()
  })
})
