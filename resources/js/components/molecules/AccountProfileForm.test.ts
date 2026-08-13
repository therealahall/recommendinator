import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AccountProfileForm from './AccountProfileForm.vue'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = { id: 1, username: 'aaron', display_name: 'Aaron Hall' }

function mountForm(props: Record<string, unknown> = {}) {
  return mount(AccountProfileForm, { props: { user: AARON, ...props } })
}

describe('AccountProfileForm', () => {
  it('opens on the account as it stands', () => {
    const wrapper = mountForm()

    expect(wrapper.find<HTMLInputElement>('#account-username').element.value).toBe('aaron')
    expect(wrapper.find<HTMLInputElement>('#account-display-name').element.value).toBe('Aaron Hall')
  })

  it('shows an empty display name rather than the word null', () => {
    const wrapper = mountForm({ user: { id: 2, username: 'bob', display_name: null } })

    expect(wrapper.find<HTMLInputElement>('#account-display-name').element.value).toBe('')
  })

  it('re-seeds from the prop when the saved account comes back changed', async () => {
    // A draft captured once at setup goes stale the moment the parent hands
    // down the values the server accepted.
    const wrapper = mountForm()

    await wrapper.setProps({ user: { id: 1, username: 'ahall', display_name: 'A. Hall' } })

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

  it('never locks the button natively, so an accepted save cannot blur it', async () => {
    // Regression: the accepted values re-seeded the fields, the button went
    // native `disabled`, and focus fell from it to <body> — past the whole
    // sidebar on the Settings page (WCAG 2.4.3).
    const wrapper = mountForm()
    const button = () => wrapper.find('[data-testid="account-profile-save"]')
    expect(button().attributes('disabled')).toBeUndefined()

    await wrapper.find('#account-display-name').setValue('Aaron')
    await wrapper.setProps({ user: { id: 1, username: 'aaron', display_name: 'Aaron' } })

    expect(button().attributes('aria-disabled')).toBe('true')
    expect(button().attributes('disabled')).toBeUndefined()
  })

  it('offers the username autocomplete so a manager updates its entry', () => {
    const wrapper = mountForm()

    expect(wrapper.find('#account-username').attributes('autocomplete')).toBe('username')
    expect(wrapper.find('#account-display-name').attributes('autocomplete')).toBe('nickname')
  })

  it('says which field it will not go without, and which it will', () => {
    // The submit button locks with aria-disabled, which is focusable and
    // silent: without this the only signal is a colour nobody can hear.
    const wrapper = mountForm()

    expect(wrapper.find('#account-username').attributes('required')).toBeDefined()
    expect(wrapper.find('#account-username').attributes('aria-required')).toBe('true')
    // "(optional)" in the label is prose; this is the programmatic state.
    expect(wrapper.find('#account-display-name').attributes('aria-required')).toBeUndefined()
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

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mountForm()

    const region = wrapper.find('#account-profile-status')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('transitions one region from saving to failed without remounting it', async () => {
    // A live region that first enters the tree already populated is read as page
    // content and skipped (WCAG 4.1.3), so the node has to outlive the refusal.
    const wrapper = mountForm()
    const region = wrapper.find('#account-profile-status').element

    await wrapper.setProps({ pending: true })
    expect(wrapper.find('#account-profile-status').text()).toBe('Saving…')

    await wrapper.setProps({ pending: false, error: 'That username is taken.' })

    expect(wrapper.find('#account-profile-status').element).toBe(region)
    expect(wrapper.find('#account-profile-status').text()).toContain('taken')
    expect(wrapper.find('#account-username').attributes('aria-describedby')).toBe(
      'account-username-hint account-profile-status',
    )
    expect(wrapper.find('#account-username').attributes('aria-invalid')).toBe('true')
  })

  it('announces a save through the same region', async () => {
    const wrapper = mountForm()
    const region = wrapper.find('#account-profile-status').element

    await wrapper.setProps({ saved: true })

    expect(wrapper.find('#account-profile-status').element).toBe(region)
    expect(wrapper.find('#account-profile-status').text()).toBe('Saved.')
  })
})
