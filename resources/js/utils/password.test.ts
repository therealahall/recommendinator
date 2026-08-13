import { describe, it, expect } from 'vitest'
import { passwordComplaint } from './password'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'

const LONG = 'x'.repeat(PASSWORD_MIN_LENGTH)

describe('passwordComplaint', () => {
  it('passes a pair that agrees and clears the length', () => {
    expect(passwordComplaint(LONG, LONG)).toBe('')
  })

  it('names the minimum, which is the rule the user has not been told', () => {
    const complaint = passwordComplaint('x'.repeat(PASSWORD_MIN_LENGTH - 1), 'x')

    expect(complaint).toContain(String(PASSWORD_MIN_LENGTH))
  })

  it('reports the length before the mismatch, since retyping fixes both', () => {
    expect(passwordComplaint('short', 'other')).toBe(
      passwordComplaint('short', 'short'),
    )
    expect(passwordComplaint(LONG, `${LONG}x`)).toContain('do not match')
  })

  it('compares untrimmed, so an account cannot be made with a password nobody can retype', () => {
    expect(passwordComplaint(LONG, `${LONG} `)).toContain('do not match')
    expect(passwordComplaint(' '.repeat(PASSWORD_MIN_LENGTH), ' '.repeat(PASSWORD_MIN_LENGTH))).toBe(
      '',
    )
  })
})
