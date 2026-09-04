import { describe, it, expect } from 'vitest'
import { passwordComplaint } from './password'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'

const LONG = 'x'.repeat(PASSWORD_MIN_LENGTH)

describe('passwordComplaint', () => {
  it('passes a pair that agrees and clears the length', () => {
    expect(passwordComplaint(LONG, LONG, PASSWORD_MIN_LENGTH)).toBe('')
  })

  it('names the minimum, which is the rule the user has not been told', () => {
    const complaint = passwordComplaint('x'.repeat(PASSWORD_MIN_LENGTH - 1), 'x', PASSWORD_MIN_LENGTH)

    expect(complaint).toContain(String(PASSWORD_MIN_LENGTH))
  })

  it('refuses against the floor it is given, which is the one the server sent', () => {
    const server = PASSWORD_MIN_LENGTH + 4
    const between = 'x'.repeat(server - 1)

    expect(passwordComplaint(between, between, PASSWORD_MIN_LENGTH)).toBe('')
    expect(passwordComplaint(between, between, server)).toContain(String(server))
  })

  it('reports the length before the mismatch, since retyping fixes both', () => {
    expect(passwordComplaint('short', 'other', PASSWORD_MIN_LENGTH)).toBe(
      passwordComplaint('short', 'short', PASSWORD_MIN_LENGTH),
    )
    expect(passwordComplaint(LONG, `${LONG}x`, PASSWORD_MIN_LENGTH)).toContain('do not match')
  })

  it('compares untrimmed, so an account cannot be made with a password nobody can retype', () => {
    const spaces = ' '.repeat(PASSWORD_MIN_LENGTH)

    expect(passwordComplaint(LONG, `${LONG} `, PASSWORD_MIN_LENGTH)).toContain('do not match')
    expect(passwordComplaint(spaces, spaces, PASSWORD_MIN_LENGTH)).toBe('')
  })
})
