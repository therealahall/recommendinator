import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AccountSection from './organisms/AccountSection.vue'
import LoginForm from './organisms/LoginForm.vue'
import SetupForm from './organisms/SetupForm.vue'
import type { UserResponse } from '@/types/api'

// Audited on the rendered tree rather than per field: a component test asserts
// the field it knows about, while a duplicate id or a dangling
// aria-describedby only exists once two of them are on screen together.

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

const SUBMIT_SURFACES: [string, () => VueWrapper][] = [
  ['SetupForm', () => mount(SetupForm)],
  ['LoginForm', () => mount(LoginForm)],
  ['AccountSection, for both its forms', () => mount(AccountSection, { props: { user: AARON } })],
]

/** The same surfaces with the request they just sent still out. */
const IN_FLIGHT_SURFACES: [string, () => VueWrapper][] = [
  ['SetupForm', () => mount(SetupForm, { props: { pending: true } })],
  ['LoginForm', () => mount(LoginForm, { props: { pending: true } })],
  [
    'AccountSection, for both its forms',
    () =>
      mount(AccountSection, {
        props: { user: AARON, profilePending: true, passwordPending: true },
      }),
  ],
]

function fields(wrapper: VueWrapper): HTMLInputElement[] {
  return Array.from(wrapper.element.querySelectorAll('input'))
}

function submitButtons(wrapper: VueWrapper): HTMLButtonElement[] {
  const buttons: HTMLButtonElement[] = Array.from(wrapper.element.querySelectorAll('button'))
  const submits = buttons.filter((button) => button.type === 'submit')
  // Guarded here so a surface that stops carrying one fails rather than
  // passing every assertion below on an empty list.
  expect(submits.length, 'no submit button on this surface').toBeGreaterThan(0)
  return submits
}

/** Fills every field, which is what takes each form off its at-rest lock. A
 *  value differing from AARON's is what makes the profile form submittable. */
async function fillEveryField(wrapper: VueWrapper): Promise<void> {
  const typed = wrapper.findAll('input')
  expect(typed.length).toBeGreaterThan(0)

  for (const field of typed) await field.setValue('a-typed-value')
}

function identifiedElements(wrapper: VueWrapper): Element[] {
  return Array.from(wrapper.element.querySelectorAll('[id]'))
}

function expectEveryFieldLabelled(wrapper: VueWrapper): void {
  const found = fields(wrapper)
  expect(found.length).toBeGreaterThan(0)

  for (const field of found) {
    expect(field.id, 'a field with no id cannot be pointed at by a label').not.toBe('')
    const label = wrapper.element.querySelector(`label[for="${field.id}"]`)
    expect(label, `no <label for="${field.id}">`).not.toBeNull()
    expect(label?.textContent?.trim()).not.toBe('')
    expect(field.getAttribute('placeholder'), 'a placeholder is not a label').toBeNull()
  }
}

function expectEveryDescriptionResolves(wrapper: VueWrapper): void {
  const present = new Set(identifiedElements(wrapper).map((element) => element.id))
  let pointers = 0

  for (const field of fields(wrapper)) {
    for (const id of (field.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)) {
      pointers += 1
      expect(present.has(id), `aria-describedby="${id}" points at nothing`).toBe(true)
    }
  }

  expect(pointers, 'no field carried a description to check').toBeGreaterThan(0)
}

function expectIdsUnique(wrapper: VueWrapper): void {
  const ids = identifiedElements(wrapper).map((element) => element.id)
  expect(ids.length).toBeGreaterThan(0)
  expect(new Set(ids).size, `duplicate id among ${ids.join(', ')}`).toBe(ids.length)
}

describe('account surface accessibility, with every message on screen', () => {
  it('SetupForm', () => {
    const wrapper = mount(SetupForm, { props: { error: 'That username is taken.' } })

    expectEveryFieldLabelled(wrapper)
    expectEveryDescriptionResolves(wrapper)
    expectIdsUnique(wrapper)
  })

  it('LoginForm', () => {
    const wrapper = mount(LoginForm, { props: { error: 'That was not accepted.' } })

    expectEveryFieldLabelled(wrapper)
    expectEveryDescriptionResolves(wrapper)
    expectIdsUnique(wrapper)
  })

  it('AccountSection, whose two forms share one page', () => {
    const wrapper = mount(AccountSection, {
      props: {
        user: AARON,
        profileError: 'That username is taken.',
        passwordError: 'That is not your current password.',
      },
    })

    expectEveryFieldLabelled(wrapper)
    expectEveryDescriptionResolves(wrapper)
    expectIdsUnique(wrapper)
  })

  it.each(SUBMIT_SURFACES)(
    '%s locks its submit without dropping it out of the tab order',
    (_name, render) => {
      // Regression: each of these switched to native `disabled` a tick after the
      // request came back, unfocusing the button under the finger that pressed
      // it and sending the user back to <body> (WCAG 2.4.3).
      for (const submit of submitButtons(render())) {
        const where = submit.textContent?.trim()
        expect(submit.getAttribute('aria-disabled'), `${where} is not locked`).toBe('true')
        expect(submit.disabled, `${where} is natively disabled`).toBe(false)
      }
    },
  )

  it.each(SUBMIT_SURFACES)(
    '%s unlocks its submit once every field is filled',
    async (_name, render) => {
      // Without this the assertion above is satisfied by a button hardcoded
      // aria-disabled, which locks the form out of use and reports as a pass.
      const wrapper = render()
      await fillEveryField(wrapper)

      for (const submit of submitButtons(wrapper)) {
        const where = submit.textContent?.trim()
        expect(submit.getAttribute('aria-disabled'), `${where} never unlocks`).toBeNull()
      }
    },
  )

  it.each(IN_FLIGHT_SURFACES)(
    '%s holds the lock in aria while its request is out',
    async (_name, render) => {
      // The lock above is the incomplete-form one. `:disabled="pending"`
      // satisfies it while unfocusing the button under the finger that just
      // pressed Enter, dropping the user on <body> (WCAG 2.4.3).
      const wrapper = render()
      await fillEveryField(wrapper)

      for (const submit of submitButtons(wrapper)) {
        const where = submit.textContent?.trim()
        expect(submit.getAttribute('aria-disabled'), `${where} is unlocked in flight`).toBe('true')
        expect(submit.disabled, `${where} is natively disabled in flight`).toBe(false)
      }
    },
  )

  it('names both live regions apart, so neither form steals the other report', () => {
    const wrapper = mount(AccountSection, { props: { user: AARON } })

    const regions = identifiedElements(wrapper).filter(
      (element) => element.getAttribute('role') === 'status',
    )
    expect(regions.map((region) => region.id)).toEqual([
      'account-profile-status',
      'account-password-status',
    ])
  })
})
