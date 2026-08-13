import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AccountSection from './AccountSection.vue'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = { id: 1, username: 'aaron', display_name: 'Aaron Hall' }

function mountSection(props: Record<string, unknown> = {}) {
  return mount(AccountSection, { props: { user: AARON, ...props } })
}

describe('AccountSection', () => {
  it('is a labelled section of the Settings page, not a route of its own', () => {
    const wrapper = mountSection()

    const section = wrapper.find('section.card')
    expect(section.attributes('aria-labelledby')).toBe('account-heading')
    expect(wrapper.find('#account-heading').text()).toBe('Account')
    expect(wrapper.findAll('h3').length).toBe(1)
    expect(wrapper.find('a').exists()).toBe(false)
  })

  it('carries both forms, each with its own submit button', () => {
    const wrapper = mountSection()

    expect(wrapper.find('[data-testid="account-profile-save"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="account-password-save"]').exists()).toBe(true)
    expect(wrapper.findAll('form').length).toBe(2)
  })

  it('forwards the profile change to its parent', async () => {
    const wrapper = mountSection()

    await wrapper.find('#account-display-name').setValue('Aaron')
    await wrapper.findAll('form')[0].trigger('submit')

    expect(wrapper.emitted('save-profile')).toEqual([[{ username: 'aaron', display_name: 'Aaron' }]])
  })

  it('forwards the password change to its parent', async () => {
    const wrapper = mountSection()

    await wrapper.find('#account-current-password').setValue('hunter2')
    await wrapper.find('#account-new-password').setValue('hunter3')
    await wrapper.find('#account-confirm-password').setValue('hunter3')
    await wrapper.findAll('form')[1].trigger('submit')

    expect(wrapper.emitted('change-password')).toEqual([
      [{ current_password: 'hunter2', new_password: 'hunter3' }],
    ])
  })

  it('offers a sign-out that is a button, not a form the browser may autofill', async () => {
    const wrapper = mountSection()

    const signOut = wrapper.find('[data-testid="account-sign-out"]')
    expect(signOut.attributes('type')).toBe('button')

    await signOut.trigger('click')

    expect(wrapper.emitted('sign-out')).toHaveLength(1)
  })

  it('keeps the two reports apart', async () => {
    // Separate endpoints, separate failures: a rejected password must not put
    // an error beside the Save details button, or vice versa.
    const wrapper = mountSection({ passwordError: 'That is not your current password.' })

    expect(wrapper.find('#account-password-status').text()).toContain('current password')
    expect(wrapper.find('#account-profile-status').text()).toBe('')

    await wrapper.setProps({ passwordError: '', profileError: 'That username is taken.' })

    expect(wrapper.find('#account-profile-status').text()).toContain('taken')
    expect(wrapper.find('#account-password-status').text()).toBe('')
  })
})
