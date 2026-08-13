import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { stringDetail } from '@/utils/apiDetail'
import type {
  LoginRequest,
  PasswordChangeRequest,
  SessionResponse,
  SetupRequest,
  UserResponse,
  UserUpdateRequest,
} from '@/types/api'

const SESSION_URL = '/api/auth/session'
const SETUP_URL = '/api/auth/setup'
const LOGIN_URL = '/api/auth/login'
const LOGOUT_URL = '/api/auth/logout'
const ACCOUNTS_URL = '/api/users'

const UNREACHABLE = 'The server did not answer. Check that it is running, then try again.'
const SETUP_REFUSED = 'That account could not be created. Check the details and try again.'
const SIGN_IN_REFUSED = 'That sign-in was not accepted. Check the details and try again.'
const SAVE_REFUSED = 'Those details could not be saved. Check them and try again.'
const PASSWORD_REFUSED = 'That password could not be changed. Check the details and try again.'
/** Exported for the shell, which says the same thing when a 401 takes it down
 *  mid-session: a screen that empties with no word reads as a crash. */
export const SESSION_ENDED = 'Your session ended. Sign in again.'

/** Which screen the app opens on. 'unknown' renders none of the three: the boot
 *  call has not answered yet, and guessing flashes a sign-in form at someone who
 *  is already signed in. */
export type SessionState = 'unknown' | 'unclaimed' | 'signed-out' | 'signed-in'

// Deliberately not useApi: that composable imports this store, and importing it
// back would close a module cycle.
function sendJson(method: string, url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** The server's own wording, which is written for the user, or *fallback* when
 *  the refusal carries a validation shape instead of a sentence. */
async function refusal(response: Response, fallback: string): Promise<string> {
  const body: unknown = await response.json().catch(() => undefined)
  return stringDetail(body) ?? fallback
}

function screenFor(session: SessionResponse): SessionState {
  if (session.authenticated) return 'signed-in'
  return session.claimed ? 'signed-out' : 'unclaimed'
}

export const useAuthStore = defineStore('auth', () => {
  // No token and nothing in localStorage: the session is an httpOnly cookie the
  // browser attaches itself, so a reload asks the server rather than a store
  // that a script on the page could read.
  const state = ref<SessionState>('unknown')
  const user = ref<UserResponse | null>(null)

  const isAuthenticated = computed(() => state.value === 'signed-in')
  const needsSetup = computed(() => state.value === 'unclaimed')
  const needsLogin = computed(() => state.value === 'signed-out')

  /** Ask the one question that decides all three screens. Returns '' when the
   *  answer arrived, or the message to show over the sign-in form. */
  async function resolveSession(): Promise<string> {
    let response: Response
    try {
      response = await fetch(SESSION_URL, { credentials: 'include' })
    } catch {
      state.value = 'signed-out'
      return UNREACHABLE
    }
    if (!response.ok) {
      state.value = 'signed-out'
      return await refusal(response, UNREACHABLE)
    }

    const session: SessionResponse = await response.json()
    user.value = session.user
    state.value = screenFor(session)
    return ''
  }

  async function claimSession(
    url: string,
    credentials: SetupRequest | LoginRequest,
    fallback: string,
  ): Promise<string> {
    let response: Response
    try {
      response = await sendJson('POST', url, credentials)
    } catch {
      return UNREACHABLE
    }
    if (!response.ok) return await refusal(response, fallback)

    user.value = await response.json()
    state.value = 'signed-in'
    return ''
  }

  /** Claim an unclaimed instance, which signs the claimant straight in. */
  function signUp(account: SetupRequest): Promise<string> {
    return claimSession(SETUP_URL, account, SETUP_REFUSED)
  }

  function signIn(credentials: LoginRequest): Promise<string> {
    return claimSession(LOGIN_URL, credentials, SIGN_IN_REFUSED)
  }

  async function signOut(): Promise<void> {
    // A logout that never reached the server means there is no server holding
    // the session open either, so the user leaves regardless.
    await fetch(LOGOUT_URL, { method: 'POST', credentials: 'include' }).catch(() => undefined)
    reject()
  }

  /** Take the shell down: the session is gone, and a store that swallowed the
   *  401 would leave a half-empty app with no way back to the sign-in form. */
  function reject(): void {
    user.value = null
    state.value = 'signed-out'
  }

  async function updateProfile(changes: UserUpdateRequest): Promise<string> {
    if (user.value === null) return SESSION_ENDED

    let response: Response
    try {
      response = await sendJson('PATCH', `${ACCOUNTS_URL}/${user.value.id}`, changes)
    } catch {
      return UNREACHABLE
    }
    if (response.status === 401) {
      // Nothing else on this route answers 401, so the session is what expired.
      reject()
      return SESSION_ENDED
    }
    if (!response.ok) return await refusal(response, SAVE_REFUSED)

    user.value = await response.json()
    return ''
  }

  async function changePassword(change: PasswordChangeRequest): Promise<string> {
    if (user.value === null) return SESSION_ENDED

    let response: Response
    try {
      response = await sendJson('PUT', `${ACCOUNTS_URL}/${user.value.id}/password`, change)
    } catch {
      return UNREACHABLE
    }
    // A 401 here answers the current-password field, not the session, so it must
    // not sign anyone out: one typo would otherwise empty the whole screen.
    if (!response.ok) return await refusal(response, PASSWORD_REFUSED)
    return ''
  }

  return {
    // State
    state,
    user,
    // Getters
    isAuthenticated,
    needsSetup,
    needsLogin,
    // Actions
    resolveSession,
    signUp,
    signIn,
    signOut,
    updateProfile,
    changePassword,
    reject,
  }
})
