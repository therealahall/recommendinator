import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import PasswordChangeForm from './PasswordChangeForm.vue'

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

    await fillIn(wrapper, { current: 'hunter2', replacement: 'hunter3', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ current_password: 'hunter2', new_password: 'hunter3' }],
    ])
  })

  it('masks all three fields and tells a manager which is which', () => {
    const wrapper = mount(PasswordChangeForm)

    for (const id of ['#account-current-password', '#account-new-password', '#account-confirm-password']) {
      expect(wrapper.find(id).attributes('type')).toBe('password')
    }
    expect(wrapper.find('#account-current-password').attributes('autocomplete')).toBe(
      'current-password',
    )
    expect(wrapper.find('#account-new-password').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.find('#account-confirm-password').attributes('autocomplete')).toBe(
      'new-password',
    )
  })

  it('refuses a mismatch locally and marks the two fields that disagree', async () => {
    const wrapper = mount(PasswordChangeForm)

    await fillIn(wrapper, { current: 'hunter2', replacement: 'hunter3', confirmation: 'hunter4' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#account-password-status').text()).toContain('do not match')
    expect(wrapper.find('#account-new-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#account-confirm-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#account-current-password').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#account-current-password').attributes('aria-describedby')).toBeUndefined()
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
    await fillIn(wrapper, { current: 'wrong', replacement: 'hunter3', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')

    await wrapper.setProps({ error: 'That is not your current password.' })

    expect(wrapper.find<HTMLInputElement>('#account-current-password').element.value).toBe('wrong')
    expect(wrapper.find<HTMLInputElement>('#account-new-password').element.value).toBe('hunter3')
  })

  it('empties the fields only once the change is confirmed', async () => {
    const wrapper = mount(PasswordChangeForm)
    await fillIn(wrapper, { current: 'hunter2', replacement: 'hunter3', confirmation: 'hunter3' })

    await wrapper.setProps({ saved: true })

    expect(wrapper.find<HTMLInputElement>('#account-current-password').element.value).toBe('')
    expect(wrapper.find<HTMLInputElement>('#account-new-password').element.value).toBe('')
    expect(wrapper.find<HTMLInputElement>('#account-confirm-password').element.value).toBe('')
    expect(wrapper.find('#account-password-status').text()).toBe('Password changed.')
  })

  it('locks the submit button until all three fields are filled', async () => {
    const wrapper = mount(PasswordChangeForm)
    expect(wrapper.find('[data-testid="account-password-save"]').attributes('disabled')).toBeDefined()

    await fillIn(wrapper, { current: 'hunter2', replacement: 'hunter3', confirmation: 'hunter3' })

    expect(
      wrapper.find('[data-testid="account-password-save"]').attributes('disabled'),
    ).toBeUndefined()
  })
})
