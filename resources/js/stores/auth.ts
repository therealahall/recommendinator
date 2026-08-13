import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ApiError, useApi } from '@/composables/useApi'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'
import { stringDetail } from '@/utils/apiDetail'
import type {
  LoginRequest,
  PasswordChangeRequest,
  SessionResponse,
  SetupRequest,
  UserResponse,
  UserUpdateRequest,
} from '@/types/api'

const SESSION_PATH = '/auth/session'
const SETUP_PATH = '/auth/setup'
const LOGIN_PATH = '/auth/login'
const LOGOUT_PATH = '/auth/logout'
const ACCOUNTS_PATH = '/users'

const UNREACHABLE = 'The server did not answer. Check that it is running, then try again.'
const SETUP_REFUSED = 'That account could not be created. Check the details and try again.'
const SIGN_IN_REFUSED = 'That sign-in was not accepted. Check the details and try again.'
const SAVE_REFUSED = 'Those details could not be saved. Check them and try again.'
const PASSWORD_REFUSED = 'That password could not be changed. Check the details and try again.'
/** Exported for the shell, which says the same thing when a 401 takes it down
 *  mid-session: a screen that empties with no word reads as a crash. */
export const SESSION_ENDED = 'Your session ended. Sign in again.'

/** Which screen the app opens on. 'unknown' renders the boot screen: the call
 *  has not answered, and guessing flashes a sign-in form at someone who is
 *  already signed in. */
export type SessionState = 'unknown' | 'unclaimed' | 'signed-out' | 'signed-in'

/** The server's own wording, which is written for the user, or *fallback* when
 *  the refusal carries a validation shape instead of a sentence. */
function messageFor(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return UNREACHABLE
  return stringDetail(error.body) ?? fallback
}

function screenFor(session: SessionResponse): SessionState {
  if (session.authenticated) return 'signed-in'
  return session.claimed ? 'signed-out' : 'unclaimed'
}

export const useAuthStore = defineStore('auth', () => {
  const api = useApi()

  // No token and nothing in localStorage: the session is an httpOnly cookie the
  // browser attaches itself, so a reload asks the server rather than a store
  // that a script on the page could read.
  const state = ref<SessionState>('unknown')
  const user = ref<UserResponse | null>(null)
  // The server owns the floor. The constant only covers the gap before the
  // session call answers, and the boot screen is what is up until it does.
  const minPasswordLength = ref(PASSWORD_MIN_LENGTH)

  const isAuthenticated = computed(() => state.value === 'signed-in')
  const needsSetup = computed(() => state.value === 'unclaimed')
  const needsLogin = computed(() => state.value === 'signed-out')

  /** Ask the one question that decides all three screens. Returns '' when the
   *  answer arrived, or the message to show over the sign-in form. */
  async function resolveSession(): Promise<string> {
    let session: SessionResponse
    try {
      session = await api.get<SessionResponse>(SESSION_PATH)
    } catch (error) {
      state.value = 'signed-out'
      return messageFor(error, UNREACHABLE)
    }

    user.value = session.user
    minPasswordLength.value = session.min_password_length
    state.value = screenFor(session)
    return ''
  }

  async function claimSession(
    path: string,
    credentials: SetupRequest | LoginRequest,
    fallback: string,
  ): Promise<string> {
    try {
      user.value = await api.post<UserResponse>(path, credentials)
    } catch (error) {
      // Another tab won the race for the first account, and "sign in instead"
      // needs a sign-in form to be advice rather than a dead end.
      if (error instanceof ApiError && error.status === 409) await resolveSession()
      return messageFor(error, fallback)
    }

    state.value = 'signed-in'
    return ''
  }

  /** Claim an unclaimed instance, which signs the claimant straight in. */
  function signUp(account: SetupRequest): Promise<string> {
    return claimSession(SETUP_PATH, account, SETUP_REFUSED)
  }

  function signIn(credentials: LoginRequest): Promise<string> {
    return claimSession(LOGIN_PATH, credentials, SIGN_IN_REFUSED)
  }

  async function signOut(): Promise<void> {
    // A logout that never reached the server means there is no server holding
    // the session open either, so the user leaves regardless.
    await api.post(LOGOUT_PATH).catch(() => undefined)
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

    try {
      user.value = await api.patch<UserResponse>(`${ACCOUNTS_PATH}/${user.value.id}`, changes)
    } catch (error) {
      // The API layer has already taken the shell down; this is the wording.
      if (error instanceof ApiError && error.status === 401) return SESSION_ENDED
      return messageFor(error, SAVE_REFUSED)
    }

    return ''
  }

  async function changePassword(change: PasswordChangeRequest): Promise<string> {
    if (user.value === null) return SESSION_ENDED

    try {
      await api.put(`${ACCOUNTS_PATH}/${user.value.id}/password`, change, {
        sessionSurvives401: true,
      })
    } catch (error) {
      return messageFor(error, PASSWORD_REFUSED)
    }

    // The 204 carries no body, so without this the account section goes on
    // showing the date of the password this call just replaced.
    await resolveSession()
    return ''
  }

  return {
    // State
    state,
    user,
    minPasswordLength,
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
