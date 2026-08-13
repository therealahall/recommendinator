import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SetupForm from './SetupForm.vue'

/** The hint ids sit in aria-describedby too, so ask what the field points at
 *  rather than pinning the whole list. */
function describedBy(wrapper: VueWrapper, selector: string): string {
  return wrapper.find(selector).attributes('aria-describedby') ?? ''
}

async function fillIn(
  wrapper: VueWrapper,
  values: { username?: string; displayName?: string; password?: string; confirmation?: string },
): Promise<void> {
  if (values.username !== undefined) await wrapper.find('#setup-username').setValue(values.username)
  if (values.displayName !== undefined) {
    await wrapper.find('#setup-display-name').setValue(values.displayName)
  }
  if (values.password !== undefined) await wrapper.find('#setup-password').setValue(values.password)
  if (values.confirmation !== undefined) {
    await wrapper.find('#setup-confirmation').setValue(values.confirmation)
  }
}

describe('SetupForm', () => {
  it('emits the account the first-run request needs', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, {
      username: ' aaron ',
      displayName: ' Aaron Hall ',
      password: 'hunter2',
      confirmation: 'hunter2',
    })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ username: 'aaron', display_name: 'Aaron Hall', password: 'hunter2' }],
    ])
  })

  it('treats the display name as optional, matching the sidebar fallback', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter2' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ username: 'aaron', display_name: '', password: 'hunter2' }],
    ])
  })

  it('offers new-password on both password fields, never current-password', () => {
    const wrapper = mount(SetupForm)

    expect(wrapper.find('#setup-password').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.find('#setup-confirmation').attributes('autocomplete')).toBe('new-password')
    expect(wrapper.find('#setup-password').attributes('type')).toBe('password')
    expect(wrapper.find('#setup-confirmation').attributes('type')).toBe('password')
    expect(wrapper.find('#setup-username').attributes('autocomplete')).toBe('username')
  })

  it('focuses the username field on mount', () => {
    const wrapper = mount(SetupForm, { attachTo: document.body })

    expect(document.activeElement).toBe(wrapper.find('#setup-username').element)
    wrapper.unmount()
  })

  it('refuses a mismatch locally and says which fields disagree', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toContain('do not match')
    expect(wrapper.find('#setup-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#setup-confirmation').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#setup-username').attributes('aria-invalid')).toBeUndefined()
  })

  it('points only the password fields at a mismatch, not the whole form', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')

    expect(describedBy(wrapper, '#setup-password')).toBe('setup-status')
    expect(describedBy(wrapper, '#setup-confirmation')).toBe('setup-status')
    expect(describedBy(wrapper, '#setup-username')).not.toContain('setup-status')
  })

  it('points every field at a server refusal, which names no field', () => {
    const wrapper = mount(SetupForm, { props: { error: 'That username is taken.' } })

    expect(describedBy(wrapper, '#setup-username')).toBe('setup-username-hint setup-status')
    expect(describedBy(wrapper, '#setup-display-name')).toBe(
      'setup-display-name-hint setup-status',
    )
    expect(describedBy(wrapper, '#setup-password')).toBe('setup-status')
  })

  it('drops the mismatch complaint on the next keystroke', async () => {
    const wrapper = mount(SetupForm)
    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')

    await fillIn(wrapper, { confirmation: 'hunter2' })

    expect(wrapper.find('#setup-status').text()).toBe('')
    expect(wrapper.find('#setup-confirmation').attributes('aria-invalid')).toBeUndefined()
  })

  it('compares the two passwords untrimmed, so a stray space is a mismatch', async () => {
    // Trimming here would create an account whose password nobody can retype.
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter2 ' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toContain('do not match')
  })

  it('keeps a password of nothing but spaces exactly as typed', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: ' aaron ', password: '  ', confirmation: '  ' })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ username: 'aaron', display_name: '', password: '  ' }],
    ])
  })

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mount(SetupForm)

    const region = wrapper.find('#setup-status')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('transitions one region through a mismatch and a refusal without remounting it', async () => {
    // A live region that first enters the tree already populated is read as page
    // content and skipped (WCAG 4.1.3), so the node has to outlive both messages.
    const wrapper = mount(SetupForm)
    const region = wrapper.find('#setup-status').element

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter3' })
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('#setup-status').element).toBe(region)
    expect(wrapper.find('#setup-status').text()).toContain('do not match')

    await fillIn(wrapper, { confirmation: 'hunter2' })
    await wrapper.setProps({ error: 'That username is taken.' })

    expect(wrapper.find('#setup-status').element).toBe(region)
    expect(wrapper.find('#setup-status').text()).toContain('taken')
  })

  it('keeps every draft after a refusal', async () => {
    const wrapper = mount(SetupForm)
    await fillIn(wrapper, {
      username: 'aaron',
      displayName: 'Aaron',
      password: 'hunter2',
      confirmation: 'hunter2',
    })
    await wrapper.find('form').trigger('submit')

    await wrapper.setProps({ error: 'That username is taken.' })

    expect(wrapper.find<HTMLInputElement>('#setup-username').element.value).toBe('aaron')
    expect(wrapper.find<HTMLInputElement>('#setup-password').element.value).toBe('hunter2')
    expect(wrapper.find<HTMLInputElement>('#setup-confirmation').element.value).toBe('hunter2')
  })

  it('locks the submit button until the required fields are filled', async () => {
    const wrapper = mount(SetupForm)
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()

    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter2' })

    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('locks the submit button without blurring it while the request is in flight', async () => {
    const wrapper = mount(SetupForm, { props: { pending: true } })
    await fillIn(wrapper, { username: 'aaron', password: 'hunter2', confirmation: 'hunter2' })
    await wrapper.find('form').trigger('submit')

    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
