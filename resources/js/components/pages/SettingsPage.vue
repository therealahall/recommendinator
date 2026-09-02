<script setup lang="ts">
import { onMounted } from 'vue'
import AccountSection from '@/components/organisms/AccountSection.vue'
import SettingsSection from '@/components/organisms/SettingsSection.vue'
import { useSubmission } from '@/composables/useSubmission'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import type { PasswordChangeRequest, UserUpdateRequest } from '@/types/api'

const store = useSettingsStore()
const auth = useAuthStore()
// Two requests, two reports: a refused password must not put an error beside
// the Save details button.
const profile = useSubmission()
const password = useSubmission()

onMounted(() => {
  store.load()
})

function saveProfile(changes: UserUpdateRequest) {
  profile.submit(() => auth.updateProfile(changes))
}

function changePassword(change: PasswordChangeRequest) {
  password.submit(() => auth.changePassword(change))
}
</script>

<template>
  <div :aria-busy="store.loading || undefined">
    <!-- aria-busy sits on this wrapper because it is the only node present in
         every outcome: assistive tech tracking the state has to hear the flag
         flip to false. -->
    <div class="page-header">
      <h2>Settings</h2>
      <p class="page-description">Application configuration for this instance.</p>
    </div>

    <div
      v-if="store.loading || store.loadError || store.sections.length === 0"
      class="card"
    >
      <div v-if="store.loading" class="state state--loading">Loading settings…</div>

      <div v-else-if="store.loadError" class="state state--error">
        <!-- The Retry button sits OUTSIDE the alert: alert content is announced
             as one chunk, which buries the control's affordance. -->
        <span role="alert">Couldn't load settings.</span>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="settings-retry"
          @click="store.load()"
        >Retry</button>
      </div>

      <div v-else class="state state--empty">No configurable settings.</div>
    </div>

    <template v-else>
      <SettingsSection
        v-for="section in store.sections"
        :key="section.section"
        :section="section"
      />
    </template>

    <AccountSection
      v-if="auth.user"
      :user="auth.user"
      :profile-error="profile.error"
      :profile-pending="profile.pending"
      :profile-saved="profile.saved"
      :password-error="password.error"
      :password-pending="password.pending"
      :password-saved="password.saved"
      :min-password-length="auth.minPasswordLength"
      @save-profile="saveProfile"
      @change-password="changePassword"
      @sign-out="auth.signOut"
    />
  </div>
</template>
