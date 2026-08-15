import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AccountProfileForm from './AccountProfileForm.vue'
import { USERNAME_BLANK } from '@/constants/auth'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

function mountForm(props: Record<string, unknown> = {}) {
  return mount(AccountProfileForm, { props: { user: AARON, ...props } })
}

describe('AccountProfileForm', () => {
  it('re-seeds from the prop when the saved account comes back changed', async () => {
    // A draft captured once at setup goes stale the moment the parent hands
    // down the values the server accepted.
    const wrapper = mountForm()

    await wrapper.setProps({ user: { ...AARON, username: 'ahall', display_name: 'A. Hall' } })

    expect(wrapper.find<HTMLInputElement>('#account-username').element.value).toBe('ahall')
    expect(wrapper.find<HTMLInputElement>('#account-display-name').element.value).toBe('A. Hall')
  })

  it('emits the trimmed change', async () => {
    const wrapper = mountForm()

    await wrapper.find('#account-display-name').setValue('  Aaron  ')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([[{ username: 'aaron', display_name: 'Aaron' }]])
  })

  it('will not save an unchanged account, or an empty username', async () => {
    const wrapper = mountForm()
    const button = () => wrapper.find('[data-testid="account-profile-save"]')
    expect(button().attributes('aria-disabled')).toBe('true')

    await wrapper.find('#account-username').setValue('')

    expect(button().attributes('aria-disabled')).toBe('true')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('says why a username of nothing but spaces did not save', async () => {
    // Regression: `required` reports only the empty string as missing, so three
    // spaces cleared the browser's own check and the handler returned silently.
    const wrapper = mountForm()

    await wrapper.find('#account-username').setValue('   ')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#account-profile-status').text()).toBe(USERNAME_BLANK)
    expect(wrapper.find('#account-username').attributes('aria-invalid')).toBe('true')
    // The lock says nothing on its own, and going native would blur the button.
    const button = wrapper.find('[data-testid="account-profile-save"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()

    await wrapper.find('#account-username').setValue('ahall')

    expect(wrapper.find('#account-profile-status').text()).toBe('')
  })

  it('will not send a second save while the first is in flight', async () => {
    const wrapper = mountForm({ pending: true })

    await wrapper.find('#account-display-name').setValue('Aaron')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(
      wrapper.find('[data-testid="account-profile-save"]').attributes('aria-disabled'),
    ).toBe('true')
  })
})
