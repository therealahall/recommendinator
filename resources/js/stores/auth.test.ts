import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('starts unauthenticated when nothing is stored', () => {
    const auth = useAuthStore()

    expect(auth.token).toBe('')
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.rejected).toBe(false)
  })

  it('seeds the token from storage, so a reload does not ask again', () => {
    localStorage.setItem('apiToken', 'stored-token')

    const auth = useAuthStore()

    expect(auth.token).toBe('stored-token')
    expect(auth.isAuthenticated).toBe(true)
  })

  it('persists a trimmed token, because a pasted one carries whitespace', () => {
    const auth = useAuthStore()

    auth.setToken('  pasted-token\n')

    expect(auth.token).toBe('pasted-token')
    expect(localStorage.getItem('apiToken')).toBe('pasted-token')
  })

  it('drops a rejected token from storage and flags it for the gate', () => {
    const auth = useAuthStore()
    auth.setToken('wrong-token')

    auth.reject()

    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('apiToken')).toBeNull()
    expect(auth.rejected).toBe(true)
  })

  it('clears the rejection when a new token is entered', () => {
    const auth = useAuthStore()
    auth.reject()

    auth.setToken('another-token')

    expect(auth.rejected).toBe(false)
  })
})
