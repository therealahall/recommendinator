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

function fields(wrapper: VueWrapper): HTMLInputElement[] {
  return Array.from(wrapper.element.querySelectorAll('input'))
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
