import { describe, it, expect, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises, type VueWrapper } from '@vue/test-utils'
import AccountSection from './AccountSection.vue'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

enableAutoUnmount(afterEach)

function renderSection(props: Record<string, unknown> = {}): VueWrapper {
  return mount(AccountSection, { props: { user: AARON, ...props }, attachTo: document.body })
}

async function mountSection(props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const wrapper = renderSection(props)
  await wrapper.find('button[aria-expanded="false"]').trigger('click')
  return wrapper
}

/** What the operator can actually reach: a collapsed panel carries `hidden`. */
function reachable(wrapper: VueWrapper, selector: string): boolean {
  return wrapper.get(selector).element.closest('[hidden]') === null
}

describe('AccountSection', () => {
  it('holds every account control behind a disclosure that arrives shut', () => {
    const wrapper = renderSection()

    expect(reachable(wrapper, '#account-display-name')).toBe(false)
    expect(reachable(wrapper, '#account-current-password')).toBe(false)
    expect(reachable(wrapper, '[data-testid="account-sign-out"]')).toBe(false)
  })

  it('keeps its heading at the level of the sections it sits beside', () => {
    const wrapper = renderSection()

    const trigger = wrapper.get('button[aria-expanded]')
    expect(trigger.element.closest('h1,h2,h3,h4,h5,h6')?.tagName).toBe('H3')
    expect(trigger.text()).toContain('Account')
  })

  it('names its panel from the trigger that controls it', () => {
    const wrapper = renderSection()

    const trigger = wrapper.get('button[aria-expanded]')
    const panel = wrapper.get(`#${trigger.attributes('aria-controls')}`)
    expect(panel.attributes('aria-labelledby')).toBe(trigger.attributes('id'))
  })

  it('forwards the profile change to its parent', async () => {
    const wrapper = await mountSection()

    await wrapper.find('#account-display-name').setValue('Aaron')
    await wrapper.findAll('form')[0].trigger('submit')

    expect(wrapper.emitted('save-profile')).toEqual([[{ username: 'aaron', display_name: 'Aaron' }]])
  })

  it('forwards the password change to its parent', async () => {
    const replacement = 'hunter3-hunter3'
    const wrapper = await mountSection()

    await wrapper.find('#account-current-password').setValue('hunter2')
    await wrapper.find('#account-new-password').setValue(replacement)
    await wrapper.find('#account-confirm-password').setValue(replacement)
    await wrapper.findAll('form')[1].trigger('submit')

    expect(wrapper.emitted('change-password')).toEqual([
      [{ current_password: 'hunter2', new_password: replacement }],
    ])
  })

  it('offers a sign-out that is a button, not a form the browser may autofill', async () => {
    const wrapper = await mountSection()

    const signOut = wrapper.find('[data-testid="account-sign-out"]')
    expect(signOut.attributes('type')).toBe('button')

    await signOut.trigger('click')

    expect(wrapper.emitted('sign-out')).toHaveLength(1)
  })

  it('hands the password form both facts only the session knows', async () => {
    const wrapper = await mountSection({ minPasswordLength: 16 })

    expect(wrapper.find('#account-new-password-hint').text()).toContain('16')
    expect(wrapper.find('[data-testid="account-password-age"]').text()).not.toContain('never')
  })

  it('keeps the two reports apart', async () => {
    const wrapper = await mountSection({ passwordError: 'That is not your current password.' })

    expect(wrapper.find('#account-password-status').text()).toContain('current password')
    expect(wrapper.find('#account-profile-status').text()).toBe('')

    await wrapper.setProps({ passwordError: '', profileError: 'That username is taken.' })

    expect(wrapper.find('#account-profile-status').text()).toContain('taken')
    expect(wrapper.find('#account-password-status').text()).toBe('')
  })

  describe('an outcome arriving while the panel is shut', () => {
    it('opens the panel on a refused profile save, and lands on the refused field', async () => {
      const wrapper = renderSection()

      await wrapper.setProps({ profileError: 'That username is taken.' })
      await flushPromises()

      expect(reachable(wrapper, '#account-profile-status')).toBe(true)
      expect(wrapper.find('#account-profile-status').text()).toContain('taken')
      expect(document.activeElement).toBe(wrapper.get('#account-username').element)
    })

    it('opens the panel on a refused password change, and lands on the refused field', async () => {
      const wrapper = renderSection()

      await wrapper.setProps({ passwordError: 'That is not your current password.' })
      await flushPromises()

      expect(reachable(wrapper, '#account-password-status')).toBe(true)
      expect(document.activeElement).toBe(wrapper.get('#account-current-password').element)
    })

    it('leaves focus alone when a save lands, having asked nothing of the operator', async () => {
      const wrapper = renderSection()
      const trigger = wrapper.get('button[aria-expanded]').element as HTMLButtonElement
      trigger.focus()

      await wrapper.setProps({ passwordSaved: true })
      await flushPromises()

      expect(document.activeElement).toBe(trigger)
    })
  })
})
