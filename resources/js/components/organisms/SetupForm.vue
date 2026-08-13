<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import {
  NAME_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  USERNAME_BLANK,
  passwordHint,
} from '@/constants/auth'
import { passwordComplaint } from '@/utils/password'
import type { SetupRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    /** Whatever the parent's account-creation request came back with. */
    error?: string
    pending?: boolean
    /** The server's floor, which the session call carries. */
    minPasswordLength?: number
  }>(),
  { error: '', pending: false, minPasswordLength: PASSWORD_MIN_LENGTH },
)

const emit = defineEmits<{
  submit: [account: SetupRequest]
}>()

const username = ref('')
const displayName = ref('')
const password = ref('')
const confirmation = ref('')

const complete = computed(
  () => username.value.trim() !== '' && password.value !== '' && confirmation.value !== '',
)

/** A complaint about the username or the password pair this screen can make on
 *  its own. */
const complaint = ref('')

/** Which fields the local complaint is about, so only those are marked and
 *  pointed at the region. A server refusal names none, and reaches all. */
const blankName = computed(() => complaint.value === USERNAME_BLANK)
const badPassword = computed(() => Boolean(complaint.value) && !blankName.value)

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(
  () => complaint.value || props.error || (props.pending ? 'Creating your account…' : ''),
)
const failed = computed(() => Boolean(complaint.value || props.error))

const usernameDescribedBy = computed(() =>
  announcement.value && !badPassword.value ? 'setup-status' : undefined,
)
const passwordDescribedBy = computed(() =>
  announcement.value && !blankName.value ? 'setup-status' : undefined,
)
// The display name is the one field no complaint here can be about, so it is
// pointed at the region only for a server-side message.
const displayNameDescribedBy = computed(() =>
  announcement.value && !complaint.value ? 'setup-status' : undefined,
)

// Editing any field a complaint could be about is the retry, so drop it on the
// keystroke rather than making the user submit again to find out it is gone.
watch([username, password, confirmation], () => {
  complaint.value = ''
})

function submit(): void {
  if (props.pending) return
  if (username.value.trim() === '') {
    complaint.value = USERNAME_BLANK
    return
  }
  if (!complete.value) return
  complaint.value = passwordComplaint(password.value, confirmation.value, props.minPasswordLength)
  if (complaint.value) return
  emit('submit', {
    username: username.value.trim(),
    display_name: displayName.value.trim(),
    password: password.value,
  })
}
</script>

<template>
  <main class="auth-screen">
    <form class="card auth-card" aria-labelledby="setup-heading" @submit.prevent="submit">
      <header class="auth-head">
        <p class="auth-eyebrow">Recommendinator</p>
        <h1 id="setup-heading" class="auth-title">Create your account</h1>
        <p class="auth-lede">
          This instance has no account yet. The one you make here is the one you
          sign in with, from any device.
        </p>
      </header>

      <div class="auth-fields">
        <AuthField
          id="setup-username"
          v-model="username"
          label="Username"
          autocomplete="username"
          hint="What you type to sign in."
          :described-by="usernameDescribedBy"
          :invalid="blankName"
          :required="true"
          :max-length="NAME_MAX_LENGTH"
          :autofocus="true"
        />
        <AuthField
          id="setup-display-name"
          v-model="displayName"
          label="Display name (optional)"
          autocomplete="nickname"
          hint="What the app calls you. Left blank, it uses your username."
          :described-by="displayNameDescribedBy"
          :max-length="NAME_MAX_LENGTH"
        />
        <AuthField
          id="setup-password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="new-password"
          :hint="passwordHint(minPasswordLength)"
          :described-by="passwordDescribedBy"
          :invalid="badPassword"
          :required="true"
        />
        <AuthField
          id="setup-confirmation"
          v-model="confirmation"
          label="Confirm password"
          type="password"
          autocomplete="new-password"
          :described-by="passwordDescribedBy"
          :invalid="badPassword"
          :required="true"
        />
      </div>

      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p
        id="setup-status"
        class="auth-status"
        :class="{ failed }"
        role="status"
      >{{ announcement }}</p>

      <!-- aria-disabled for both locks, never native disabled: a button that
           goes disabled under the finger that just pressed Enter throws focus
           to <body> (WCAG 2.4.3). -->
      <button
        type="submit"
        class="btn btn-primary auth-submit"
        :aria-disabled="pending || !complete ? 'true' : undefined"
      >
        Create account
      </button>
    </form>
  </main>
</template>
