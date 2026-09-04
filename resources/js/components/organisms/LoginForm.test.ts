import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import LoginForm from './LoginForm.vue'
import { USERNAME_BLANK } from '@/constants/auth'


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

  it('says why a username of nothing but spaces did not sign in', async () => {
    const wrapper = mount(LoginForm)

    await signIn(wrapper, '   ', 'hunter2')

    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(wrapper.find('#login-status').text()).toBe(USERNAME_BLANK)
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('#login-username').attributes('aria-describedby')).toBe('login-status')
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-describedby')).toBeUndefined()
    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()

    await wrapper.find('#login-username').setValue('aaron')

    expect(wrapper.find('#login-status').text()).toBe('')
  })

  it('keeps the username after a refusal, so it is not retyped on a phone', async () => {
    const wrapper = mount(LoginForm)
    await signIn(wrapper, 'aaron', 'nearly-right')

    await wrapper.setProps({ error: 'Not accepted.' })

    expect(wrapper.find<HTMLInputElement>('#login-username').element.value).toBe('aaron')
  })

  it('clears the password after a refusal, which is where the typo was', async () => {
    const wrapper = mount(LoginForm)
    await signIn(wrapper, 'aaron', 'nearly-right')

    await wrapper.setProps({ error: 'Not accepted.' })

    expect(wrapper.find<HTMLInputElement>('#login-password').element.value).toBe('')
    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
  })
})
