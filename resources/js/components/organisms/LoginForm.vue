<script setup lang="ts">
import { computed, ref } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import type { LoginRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    /** Whatever the parent's sign-in request came back with. */
    error?: string
    pending?: boolean
  }>(),
  { error: '', pending: false },
)

const emit = defineEmits<{
  submit: [credentials: LoginRequest]
}>()

const username = ref('')
const password = ref('')

const complete = computed(() => username.value.trim() !== '' && password.value !== '')

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => props.error || (props.pending ? 'Signing in…' : ''))
const failed = computed(() => Boolean(props.error))
const describedBy = computed(() => (announcement.value ? 'login-status' : undefined))

// Both drafts survive a refusal. Retyping a password on a phone keyboard is the
// whole cost of one wrong character, and the phone is why this screen exists.
function submit(): void {
  if (props.pending || !complete.value) return
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
          :described-by="describedBy"
          :invalid="failed"
          :autofocus="true"
        />
        <AuthField
          id="login-password"
          v-model="password"
          label="Password"
          type="password"
          autocomplete="current-password"
          :described-by="describedBy"
          :invalid="failed"
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

      <button
        type="submit"
        class="btn btn-primary auth-submit"
        :disabled="!complete"
        :aria-disabled="pending ? 'true' : undefined"
      >
        Sign in
      </button>
    </form>
  </main>
</template>
