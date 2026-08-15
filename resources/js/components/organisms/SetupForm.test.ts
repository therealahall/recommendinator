import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SetupForm from './SetupForm.vue'
import { PASSWORD_MIN_LENGTH, USERNAME_BLANK } from '@/constants/auth'

/** The hint ids sit in aria-describedby too, so ask what the field points at
 *  rather than pinning the whole list. */
function describedBy(wrapper: VueWrapper, selector: string): string {
  return wrapper.find(selector).attributes('aria-describedby') ?? ''
}

// Long enough to clear the length rule, so a test about anything else is not
// answered by it.
const LONG = 'hunter2-hunter2'
const OTHER = 'hunter3-hunter3'

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
      password: LONG,
      confirmation: LONG,
    })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ username: 'aaron', display_name: 'Aaron Hall', password: LONG }],
    ])
  })

  it('treats the display name as optional, matching the sidebar fallback', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toEqual([
      [{ username: 'aaron', display_name: '', password: LONG }],
    ])
  })

  it('states and enforces the floor the session reported, not one of its own', async () => {
    // Regression: the floor was compiled into the bundle, so a server enforcing
    // another one had the first screen of the app inviting a password it would
    // then refuse — and no account could be created without guessing why.
    const server = PASSWORD_MIN_LENGTH + 4
    const between = 'x'.repeat(server - 1)
    const wrapper = mount(SetupForm, { props: { minPasswordLength: server } })

    expect(wrapper.find('#setup-password-hint').text()).toContain(String(server))

    await fillIn(wrapper, { username: 'aaron', password: between, confirmation: between })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toContain(String(server))
  })

  it('refuses a mismatch locally and says which fields disagree', async () => {
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: LONG, confirmation: OTHER })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toContain('do not match')
    expect(wrapper.find('#setup-password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#setup-confirmation').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#setup-username').attributes('aria-invalid')).toBeUndefined()
  })

  it('says why a username of nothing but spaces created no account', async () => {
    // Regression: `required` reports only the empty string as missing, so three
    // spaces cleared the browser's own check and the press did nothing at all —
    // on the one screen a first-run user cannot skip.
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: '   ', password: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toBe(USERNAME_BLANK)
    expect(wrapper.find('#setup-username').attributes('aria-invalid')).toBe('true')
    expect(describedBy(wrapper, '#setup-username')).toContain('setup-status')
    // The complaint is about the name, so the passwords are neither marked nor
    // pointed at it.
    expect(wrapper.find('#setup-password').attributes('aria-invalid')).toBeUndefined()
    expect(describedBy(wrapper, '#setup-password')).not.toContain('setup-status')

    await fillIn(wrapper, { username: 'aaron' })

    expect(wrapper.find('#setup-status').text()).toBe('')
  })

  it('compares the two passwords untrimmed, so a stray space is a mismatch', async () => {
    // Trimming here would create an account whose password nobody can retype.
    const wrapper = mount(SetupForm)

    await fillIn(wrapper, { username: 'aaron', password: LONG, confirmation: `${LONG} ` })
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#setup-status').text()).toContain('do not match')
  })

  it('keeps every draft after a refusal', async () => {
    const wrapper = mount(SetupForm)
    await fillIn(wrapper, {
      username: 'aaron',
      displayName: 'Aaron',
      password: LONG,
      confirmation: LONG,
    })
    await wrapper.find('form').trigger('submit')

    await wrapper.setProps({ error: 'That username is taken.' })

    expect(wrapper.find<HTMLInputElement>('#setup-username').element.value).toBe('aaron')
    expect(wrapper.find<HTMLInputElement>('#setup-password').element.value).toBe(LONG)
    expect(wrapper.find<HTMLInputElement>('#setup-confirmation').element.value).toBe(LONG)
  })

  it('locks the submit button without blurring it while the request is in flight', async () => {
    const wrapper = mount(SetupForm, { props: { pending: true } })
    await fillIn(wrapper, { username: 'aaron', password: LONG, confirmation: LONG })
    await wrapper.find('form').trigger('submit')

    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
