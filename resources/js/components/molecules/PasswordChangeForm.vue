<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import { PASSWORD_MISMATCH } from '@/constants/auth'
import type { PasswordChangeRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    /** Whatever the parent's password-change request came back with. */
    error?: string
    pending?: boolean
    saved?: boolean
  }>(),
  { error: '', pending: false, saved: false },
)

const emit = defineEmits<{
  submit: [change: PasswordChangeRequest]
}>()

const current = ref('')
const replacement = ref('')
const confirmation = ref('')

const complete = computed(
  () => current.value !== '' && replacement.value !== '' && confirmation.value !== '',
)

const mismatch = ref(false)

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => {
  if (mismatch.value) return PASSWORD_MISMATCH
  if (props.error) return props.error
  if (props.pending) return 'Changing your password…'
  return props.saved ? 'Password changed.' : ''
})
const failed = computed(() => mismatch.value || Boolean(props.error))

// A mismatch concerns the new password and its confirmation; the current
// password is only pointed at the region for a server-side message.
const newPasswordDescribedBy = computed(() =>
  announcement.value ? 'account-password-status' : undefined,
)
const currentDescribedBy = computed(() =>
  announcement.value && !mismatch.value ? 'account-password-status' : undefined,
)

// Editing either half is the retry, so drop the complaint on the keystroke
// rather than making the user submit again to find out it is gone.
watch([replacement, confirmation], () => {
  mismatch.value = false
})

// Only a confirmed change empties the fields. A refusal leaves all three, so a
// rejected change costs one correction rather than three retypes.
watch(
  () => props.saved,
  (saved) => {
    if (!saved) return
    current.value = ''
    replacement.value = ''
    confirmation.value = ''
  },
)

function submit(): void {
  if (props.pending || !complete.value) return
  if (replacement.value !== confirmation.value) {
    mismatch.value = true
    return
  }
  emit('submit', { current_password: current.value, new_password: replacement.value })
}
</script>

<template>
  <form aria-labelledby="account-password-heading" @submit.prevent="submit">
    <h4 id="account-password-heading" class="account-form-heading">Change password</h4>

    <div class="auth-fields">
      <AuthField
        id="account-current-password"
        v-model="current"
        label="Current password"
        type="password"
        autocomplete="current-password"
        :described-by="currentDescribedBy"
        :invalid="Boolean(error)"
      />
      <AuthField
        id="account-new-password"
        v-model="replacement"
        label="New password"
        type="password"
        autocomplete="new-password"
        :described-by="newPasswordDescribedBy"
        :invalid="mismatch"
      />
      <AuthField
        id="account-confirm-password"
        v-model="confirmation"
        label="Confirm new password"
        type="password"
        autocomplete="new-password"
        :described-by="newPasswordDescribedBy"
        :invalid="mismatch"
      />
    </div>

    <div class="account-form-actions">
      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p
        id="account-password-status"
        class="auth-status"
        :class="{ failed }"
        role="status"
      >{{ announcement }}</p>

      <button
        type="submit"
        class="btn btn-primary auth-submit"
        data-testid="account-password-save"
        :disabled="!complete"
        :aria-disabled="pending ? 'true' : undefined"
      >
        Change password
      </button>
    </div>
  </form>
</template>
