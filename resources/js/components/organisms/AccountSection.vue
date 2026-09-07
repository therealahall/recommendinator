<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import AccountProfileForm from '@/components/molecules/AccountProfileForm.vue'
import PasswordChangeForm from '@/components/molecules/PasswordChangeForm.vue'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'
import type { UserResponse, UserUpdateRequest, PasswordChangeRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    user: UserResponse
    /** The two forms are saved by separate requests, so each owns its own state. */
    profileError?: string
    profilePending?: boolean
    profileSaved?: boolean
    passwordError?: string
    passwordPending?: boolean
    passwordSaved?: boolean
    /** The server's floor, which the session call carries. */
    minPasswordLength?: number
  }>(),
  {
    profileError: '',
    profilePending: false,
    profileSaved: false,
    passwordError: '',
    passwordPending: false,
    passwordSaved: false,
    minPasswordLength: PASSWORD_MIN_LENGTH,
  },
)

const emit = defineEmits<{
  'save-profile': [changes: UserUpdateRequest]
  'change-password': [change: PasswordChangeRequest]
  'sign-out': []
}>()

const expanded = ref(false)

// Only a refusal opens the panel, and focus is what carries it: the status line
// describing that field is a region unhidden with text already in it, which no
// screen reader announces. A landed save asks nothing of the operator.
function reveal(refusedFieldId: string): void {
  if (expanded.value) return
  expanded.value = true
  void nextTick().then(() => document.getElementById(refusedFieldId)?.focus())
}

watch(
  () => props.profileError,
  (error) => {
    if (error) reveal('account-username')
  },
)
watch(
  () => props.passwordError,
  (error) => {
    if (error) reveal('account-current-password')
  },
)
</script>

<template>
  <Accordion
    id="account"
    class="account-section"
    :heading-level="3"
    :expanded="expanded"
    @update:expanded="expanded = $event"
  >
    <template #header>
      <span class="settings-section-name">Account</span>
    </template>

    <p class="auth-lede account-intro">How you sign in to this instance.</p>

    <AccountProfileForm
      :user="user"
      :error="profileError"
      :pending="profilePending"
      :saved="profileSaved"
      @submit="emit('save-profile', $event)"
    />

    <hr class="account-divider" />

    <PasswordChangeForm
      :error="passwordError"
      :pending="passwordPending"
      :saved="passwordSaved"
      :min-password-length="minPasswordLength"
      :password-updated-at="user.password_updated_at"
      @submit="emit('change-password', $event)"
    />

    <hr class="account-divider" />

    <div class="account-form-actions">
      <p class="auth-status">Signing out ends this browser's session. Others stay signed in.</p>
      <button
        type="button"
        class="btn btn-secondary"
        data-testid="account-sign-out"
        @click="emit('sign-out')"
      >
        Sign out
      </button>
    </div>
  </Accordion>
</template>

<style scoped>
.account-section {
  margin-bottom: var(--space-4);
}

.account-intro {
  margin-bottom: var(--space-5);
}

.account-divider {
  border: none;
  border-top: 1px solid var(--border-default);
  margin: var(--space-6) 0;
}
</style>
