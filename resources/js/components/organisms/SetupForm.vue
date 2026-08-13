<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import { PASSWORD_MISMATCH } from '@/constants/auth'
import type { SetupRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    /** Whatever the parent's account-creation request came back with. */
    error?: string
    pending?: boolean
  }>(),
  { error: '', pending: false },
)

const emit = defineEmits<{
  submit: [account: SetupRequest]
}>()

const username = ref('')
const displayName = ref('')
const password = ref('')
const confirmation = ref('')

// Only that the required fields are filled. Length and shape are the server's
// rules, and a second copy here would drift the first time they change.
const complete = computed(
  () => username.value.trim() !== '' && password.value !== '' && confirmation.value !== '',
)

const mismatch = ref(false)

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => {
  if (mismatch.value) return PASSWORD_MISMATCH
  return props.error || (props.pending ? 'Creating your account…' : '')
})
const failed = computed(() => mismatch.value || Boolean(props.error))

// A mismatch concerns the two password fields and nothing else, so the username
// and display name are only pointed at the region for a server-side message.
const passwordDescribedBy = computed(() => (announcement.value ? 'setup-status' : undefined))
const generalDescribedBy = computed(() =>
  announcement.value && !mismatch.value ? 'setup-status' : undefined,
)

// Editing either half is the retry, so drop the complaint on the keystroke
// rather than making the user submit again to find out it is gone.
watch([password, confirmation], () => {
  mismatch.value = false
})

function submit(): void {
  if (props.pending || !complete.value) return
  if (password.value !== confirmation.value) {
    mismatch.value = true
    return
  }
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
          :described-by="generalDescribedBy"
          :autofocus="true"
        />
        <AuthField
          id="setup-display-name"
          v-model="displayName"
          label="Display name (optional)"
          autocomplete="nickname"
          hint="What the app calls you. Left blank, it uses your username."
          :described-by="generalDescribedBy"
        />
        <AuthField
          id="setup-password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="new-password"
          :described-by="passwordDescribedBy"
          :invalid="mismatch"
        />
        <AuthField
          id="setup-confirmation"
          v-model="confirmation"
          label="Confirm password"
          type="password"
          autocomplete="new-password"
          :described-by="passwordDescribedBy"
          :invalid="mismatch"
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

      <button
        type="submit"
        class="btn btn-primary auth-submit"
        :disabled="!complete"
        :aria-disabled="pending ? 'true' : undefined"
      >
        Create account
      </button>
    </form>
  </main>
</template>
