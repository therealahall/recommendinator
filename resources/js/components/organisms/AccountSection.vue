<script setup lang="ts">
import AccountProfileForm from '@/components/molecules/AccountProfileForm.vue'
import PasswordChangeForm from '@/components/molecules/PasswordChangeForm.vue'
import type { UserResponse, UserUpdateRequest, PasswordChangeRequest } from '@/types/api'

withDefaults(
  defineProps<{
    user: UserResponse
    /** The two forms are saved by separate requests, so each owns its own state. */
    profileError?: string
    profilePending?: boolean
    profileSaved?: boolean
    passwordError?: string
    passwordPending?: boolean
    passwordSaved?: boolean
  }>(),
  {
    profileError: '',
    profilePending: false,
    profileSaved: false,
    passwordError: '',
    passwordPending: false,
    passwordSaved: false,
  },
)

const emit = defineEmits<{
  'save-profile': [changes: UserUpdateRequest]
  'change-password': [change: PasswordChangeRequest]
}>()
</script>

<template>
  <section class="card" aria-labelledby="account-heading">
    <h3 id="account-heading">Account</h3>
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
      @submit="emit('change-password', $event)"
    />
  </section>
</template>

<style scoped>
.account-intro {
  margin-bottom: var(--space-5);
}

.account-divider {
  border: none;
  border-top: 1px solid var(--border-default);
  margin: var(--space-6) 0;
}
</style>
