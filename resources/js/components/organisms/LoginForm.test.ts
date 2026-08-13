import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import LoginForm from './LoginForm.vue'
import { USERNAME_BLANK } from '@/constants/auth'

// No pinia and no fetch stub anywhere in this file: the component takes props
// in and emits events out, which is what lets it ship before the routes exist.

async function signIn(wrapper: VueWrapper, username: string, password: string): Promise<void> {
  await wrapper.find('#login-username').setValue(username)
  await wrapper.find('#login-password').setValue(password)
  await wrapper.find('form').trigger('submit')
}

describe('LoginForm', () => {
  it('emits the credentials, with the username trimmed', async () => {
    const wrapper = mount(LoginForm)

    await signIn(wrapper, '  aaron  ', 'hunter2 ')

    expect(wrapper.emitted('submit')).toEqual([[{ username: 'aaron', password: 'hunter2 ' }]])
  })

  it('is a real form with a real submit button, so autofill can drive it', () => {
    // Regression guard for the defect this screen replaces: a token pasted into
    // a lone text box is unreachable from a phone's password manager.
    const wrapper = mount(LoginForm)

    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
    expect(wrapper.find('#login-username').attributes('autocomplete')).toBe('username')
    expect(wrapper.find('#login-password').attributes('autocomplete')).toBe('current-password')
    expect(wrapper.find('#login-password').attributes('type')).toBe('password')
  })

  it('focuses the username field, the first thing this screen wants', () => {
    const wrapper = mount(LoginForm, { attachTo: document.body })

    expect(document.activeElement).toBe(wrapper.find('#login-username').element)
    wrapper.unmount()
  })

  it('will not submit half a credential', async () => {
    const wrapper = mount(LoginForm)

    await wrapper.find('#login-username').setValue('aaron')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('button[type="submit"]').attributes('aria-disabled')).toBe('true')
  })

  it('says why a username of nothing but spaces did not sign in', async () => {
    // Regression: `required` reports only the empty string as missing, so three
    // spaces cleared the browser's own check and the press did nothing at all.
    const wrapper = mount(LoginForm)

    await signIn(wrapper, '   ', 'hunter2')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#login-status').text()).toBe(USERNAME_BLANK)
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#login-username').attributes('aria-describedby')).toBe('login-status')
    // The complaint is about the name, so the password is left unfaulted.
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-describedby')).toBeUndefined()
    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()

    await wrapper.find('#login-username').setValue('aaron')

    expect(wrapper.find('#login-status').text()).toBe('')
  })

  it('complains about the blank name before the gate that would return silently', async () => {
    // Both halves are missing here, and the completeness check has nothing to
    // say. Guard order is what decides whether anything is announced at all.
    const wrapper = mount(LoginForm)

    await signIn(wrapper, ' ', '')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#login-status').text()).toBe(USERNAME_BLANK)
    expect(wrapper.find('#login-status').classes()).toContain('failed')
  })

  it('says which fields it will not go without', () => {
    // The submit button locks with aria-disabled, which is focusable and
    // silent: without this the only signal is a colour nobody can hear.
    const wrapper = mount(LoginForm)

    for (const id of ['#login-username', '#login-password']) {
      expect(wrapper.find(id).attributes('required'), id).toBeDefined()
      expect(wrapper.find(id).attributes('aria-required'), id).toBe('true')
    }
  })

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mount(LoginForm)

    const region = wrapper.find('#login-status')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('transitions one region through the whole attempt without remounting it', async () => {
    // A live region that first enters the tree already populated is read as page
    // content and skipped (WCAG 4.1.3), so the node has to outlive the refusal.
    const wrapper = mount(LoginForm, { props: { pending: false, error: '' } })
    const region = wrapper.find('#login-status').element

    await wrapper.setProps({ pending: true })
    expect(wrapper.find('#login-status').text()).toBe('Signing in…')

    await wrapper.setProps({ pending: false, error: 'That username and password were not accepted.' })

    expect(wrapper.find('#login-status').element).toBe(region)
    expect(wrapper.find('#login-status').text()).toContain('not accepted')
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBe('true')
  })

  it('says why the screen is on without blaming a field nobody touched', async () => {
    // Regression: a sign-out arrived as `error`, so an empty form announced
    // "Username, invalid entry" before the user had typed anything.
    const wrapper = mount(LoginForm, { props: { notice: 'Your session ended. Sign in again.' } })

    expect(wrapper.find('#login-status').text()).toContain('session ended')
    expect(wrapper.find('#login-status').classes()).not.toContain('failed')
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()

    await wrapper.setProps({ error: 'That sign-in was not accepted.' })

    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBe('true')
  })

  it('lets the attempt in progress speak over the notice that preceded it', async () => {
    const wrapper = mount(LoginForm, { props: { notice: 'Your session ended. Sign in again.' } })

    await wrapper.setProps({ pending: true })

    expect(wrapper.find('#login-status').text()).toBe('Signing in…')
  })

  it('describes the fields by the region only while it has something to say', async () => {
    const wrapper = mount(LoginForm)
    expect(wrapper.find('#login-username').attributes('aria-describedby')).toBeUndefined()

    await wrapper.setProps({ error: 'Not accepted.' })

    expect(wrapper.find('#login-username').attributes('aria-describedby')).toBe('login-status')
    expect(wrapper.find('#login-password').attributes('aria-describedby')).toBe('login-status')
  })

  it('keeps the username after a refusal, so it is not retyped on a phone', async () => {
    const wrapper = mount(LoginForm)
    await signIn(wrapper, 'aaron', 'nearly-right')

    await wrapper.setProps({ error: 'Not accepted.' })

    expect(wrapper.find<HTMLInputElement>('#login-username').element.value).toBe('aaron')
  })

  it('clears the password after a refusal, which is where the typo was', async () => {
    // Masked and unproofreadable: retyping it is the shorter path to a correct
    // one than hunting the wrong character in a row of dots.
    const wrapper = mount(LoginForm)
    await signIn(wrapper, 'aaron', 'nearly-right')

    await wrapper.setProps({ error: 'Not accepted.' })

    expect(wrapper.find<HTMLInputElement>('#login-password').element.value).toBe('')
    // Regression: clearing the password made the form incomplete, and the
    // native `disabled` that followed unfocused the button the user had just
    // pressed Enter on, dropping focus to <body> (WCAG 2.4.3).
    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('leaves the password alone while the attempt is still open', async () => {
    const wrapper = mount(LoginForm)
    await signIn(wrapper, 'aaron', 'hunter2')

    await wrapper.setProps({ pending: true })

    expect(wrapper.find<HTMLInputElement>('#login-password').element.value).toBe('hunter2')
  })

  it('locks the submit button without blurring it while the request is in flight', async () => {
    const wrapper = mount(LoginForm, { props: { pending: true } })
    await signIn(wrapper, 'aaron', 'hunter2')

    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
