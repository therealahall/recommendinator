<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import { USERNAME_BLANK } from '@/constants/auth'
import type { LoginRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    error?: string
    /** Why this screen is on: a session that ended, or an instance another tab
     *  claimed first. Said, but never blamed on a field nobody has touched. */
    notice?: string
    pending?: boolean
  }>(),
  { error: '', notice: '', pending: false },
)

const emit = defineEmits<{
  submit: [credentials: LoginRequest]
}>()

const username = ref('')
const password = ref('')

const complete = computed(() => username.value.trim() !== '' && password.value !== '')

const complaint = ref('')

// Typing in either field is the retry, so drop the complaint on the keystroke.
watch([username, password], () => {
  complaint.value = ''
})

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(
  () => complaint.value || props.error || (props.pending ? 'Signing in…' : props.notice),
)
const failed = computed(() => Boolean(complaint.value || props.error))
const usernameDescribedBy = computed(() => (announcement.value ? 'login-status' : undefined))
// A refusal names neither field and so marks both; the complaint is about the
// username alone, and a password nobody has faulted stays unmarked.
const passwordDescribedBy = computed(() =>
  announcement.value && !complaint.value ? 'login-status' : undefined,
)
const passwordFailed = computed(() => Boolean(props.error))

// The username survives a refusal and the password does not: retyping a name on
// a phone keyboard is the friction this screen exists to remove, and a masked
// field nobody can proofread is where the wrong character usually is.
watch(
  () => props.error,
  (message) => {
    if (message) password.value = ''
  },
)

function submit(): void {
  if (props.pending) return
  if (username.value.trim() === '') {
    complaint.value = USERNAME_BLANK
    return
  }
  if (!complete.value) return
  emit('submit', { username: username.value.trim(), password: password.value })
}
</script>

<template>
  <main class="auth-screen">
    <form class="card auth-card" aria-labelledby="login-heading" @submit.prevent="submit">
      <header class="auth-head">
        <p class="auth-eyebrow">Recommendinator</p>
        <h1 id="login-heading" class="auth-title">Sign in</h1>
      </header>

      <div class="auth-fields">
        <AuthField
          id="login-username"
          v-model="username"
          label="Username"
          autocomplete="username"
          :described-by="usernameDescribedBy"
          :invalid="failed"
          :required="true"
          :autofocus="true"
        />
        <AuthField
          id="login-password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="current-password"
          :described-by="passwordDescribedBy"
          :invalid="passwordFailed"
          :required="true"
        />
      </div>

      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p
        id="login-status"
        class="auth-status"
        :class="{ failed }"
        role="status"
      >{{ announcement }}</p>

      <!-- aria-disabled for both locks, never native disabled: a refusal clears
           the password, and a button that goes disabled under the finger that
           just pressed Enter throws focus to <body> (WCAG 2.4.3). -->
      <button
        type="submit"
        class="btn btn-primary auth-submit"
        :aria-disabled="pending || !complete ? 'true' : undefined"
      >
        Sign in
      </button>
    </form>
  </main>
</template>
