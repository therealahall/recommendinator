<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import { PASSWORD_MIN_LENGTH, passwordHint } from '@/constants/auth'
import { formatDate } from '@/utils/format'
import { passwordComplaint } from '@/utils/password'
import type { PasswordChangeRequest } from '@/types/api'

const props = withDefaults(
  defineProps<{
    error?: string
    pending?: boolean
    saved?: boolean
    /** The server's floor, which the session call carries. */
    minPasswordLength?: number
    /** When this password was last set, or null if not since setup. */
    passwordUpdatedAt?: string | null
  }>(),
  {
    error: '',
    pending: false,
    saved: false,
    minPasswordLength: PASSWORD_MIN_LENGTH,
    passwordUpdatedAt: null,
  },
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

/** The same fact `account show` reports, worded the same way. */
const changedOn = computed(() =>
  props.passwordUpdatedAt ? formatDate(props.passwordUpdatedAt) : 'never',
)

const complaint = ref('')

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => {
  if (complaint.value) return complaint.value
  if (props.error) return props.error
  if (props.pending) return 'Changing your password…'
  return props.saved ? 'Password changed.' : ''
})
const failed = computed(() => Boolean(complaint.value || props.error))

// A complaint concerns the new password and its confirmation; the current
// password is only pointed at the region for a server-side message.
const newPasswordDescribedBy = computed(() =>
  announcement.value ? 'account-password-status' : undefined,
)
const currentDescribedBy = computed(() =>
  announcement.value && !complaint.value ? 'account-password-status' : undefined,
)

// Editing either half is the retry, so drop the complaint on the keystroke
// rather than making the user submit again to find out it is gone.
watch([replacement, confirmation], () => {
  complaint.value = ''
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
  complaint.value = passwordComplaint(
    replacement.value,
    confirmation.value,
    props.minPasswordLength,
  )
  if (complaint.value) return
  emit('submit', { current_password: current.value, new_password: replacement.value })
}
</script>

<template>
  <form aria-labelledby="account-password-heading" @submit.prevent="submit">
    <h4 id="account-password-heading" class="account-form-heading">Change password</h4>
    <p class="auth-lede password-age" data-testid="account-password-age">
      Password changed: {{ changedOn }}
    </p>

    <div class="auth-fields">
      <AuthField
        id="account-current-password"
        v-model="current"
        label="Current password"
        type="password"
        autocomplete="current-password"
        :described-by="currentDescribedBy"
        :required="true"
      />
      <AuthField
        id="account-new-password"
        v-model="replacement"
        label="New password"
        type="password"
        autocomplete="new-password"
        :hint="passwordHint(minPasswordLength)"
        :described-by="newPasswordDescribedBy"
        :invalid="Boolean(complaint)"
        :required="true"
      />
      <AuthField
        id="account-confirm-password"
        v-model="confirmation"
        label="Confirm new password"
        type="password"
        autocomplete="new-password"
        :described-by="newPasswordDescribedBy"
        :invalid="Boolean(complaint)"
        :required="true"
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

      <!-- aria-disabled for both locks, never native disabled: a save empties
           all three fields, and a button that goes disabled under the finger
           that just pressed Enter throws focus to <body> (WCAG 2.4.3). -->
      <button
        type="submit"
        class="btn btn-primary auth-submit"
        data-testid="account-password-save"
        :aria-disabled="pending || !complete ? 'true' : undefined"
      >
        Change password
      </button>
    </div>
  </form>
</template>

<style scoped>
/* The line belongs to the heading above it, so the heading's own gap moves
   below the pair rather than splitting them. */
.account-form-heading {
  margin-bottom: var(--space-1);
}

.password-age {
  margin-bottom: var(--space-4);
}
</style>
