<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AuthField from '@/components/atoms/AuthField.vue'
import { NAME_MAX_LENGTH } from '@/constants/auth'
import type { UserResponse, UserUpdateRequest } from '@/types/api'

/** The one lock no browser narrates: the fields are filled and valid, so
 *  constraint validation has nothing to say about a press that does nothing. */
const NOTHING_TO_SAVE = 'Nothing to save yet. Edit a field first.'

const props = withDefaults(
  defineProps<{
    user: UserResponse
    /** Whatever the parent's update request came back with. */
    error?: string
    pending?: boolean
    saved?: boolean
  }>(),
  { error: '', pending: false, saved: false },
)

const emit = defineEmits<{
  submit: [changes: UserUpdateRequest]
}>()

const username = ref('')
const displayName = ref('')

// Re-seeded from the prop rather than captured once, so the accepted values
// replace the draft after a save instead of leaving a stale copy on screen.
watch(
  () => props.user,
  (user) => {
    username.value = user.username
    displayName.value = user.display_name ?? ''
  },
  { immediate: true },
)

const changed = computed(
  () =>
    username.value.trim() !== props.user.username ||
    displayName.value.trim() !== (props.user.display_name ?? ''),
)
const submittable = computed(() => username.value.trim() !== '' && changed.value)

/** A complaint about the press this form can make on its own. */
const complaint = ref('')

// Editing either field is the retry, so drop the complaint on the keystroke.
watch([username, displayName], () => {
  complaint.value = ''
})

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => {
  if (complaint.value) return complaint.value
  if (props.error) return props.error
  if (props.pending) return 'Saving…'
  return props.saved ? 'Saved.' : ''
})
// The complaint is not a refusal of anything typed: nothing was. Only a server
// error paints the region red and marks the field it could not save.
const failed = computed(() => Boolean(props.error))
const describedBy = computed(() => (announcement.value ? 'account-profile-status' : undefined))

function submit(): void {
  if (props.pending) return
  if (!changed.value) {
    complaint.value = NOTHING_TO_SAVE
    return
  }
  // An empty username reaches here only under a test harness: `required` stops
  // the browser's own submit, and the bubble it shows is the narration.
  if (!submittable.value) return
  emit('submit', {
    username: username.value.trim(),
    display_name: displayName.value.trim(),
  })
}
</script>

<template>
  <form aria-labelledby="account-profile-heading" @submit.prevent="submit">
    <h4 id="account-profile-heading" class="account-form-heading">Who you are</h4>

    <div class="auth-fields">
      <AuthField
        id="account-username"
        v-model="username"
        label="Username"
        autocomplete="username"
        hint="What you type to sign in."
        :described-by="describedBy"
        :invalid="failed"
        :required="true"
        :max-length="NAME_MAX_LENGTH"
      />
      <AuthField
        id="account-display-name"
        v-model="displayName"
        label="Display name (optional)"
        autocomplete="nickname"
        hint="What the app calls you. Left blank, it uses your username."
        :described-by="describedBy"
        :max-length="NAME_MAX_LENGTH"
      />
    </div>

    <div class="account-form-actions">
      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p
        id="account-profile-status"
        class="auth-status"
        :class="{ failed }"
        role="status"
      >{{ announcement }}</p>

      <!-- aria-disabled for both locks, never native disabled: an accepted save
           re-seeds the fields and makes this unsubmittable, and a button that
           goes disabled under the finger that just pressed Enter throws focus
           to <body> (WCAG 2.4.3). -->
      <button
        type="submit"
        class="btn btn-primary auth-submit"
        data-testid="account-profile-save"
        :aria-disabled="pending || !submittable ? 'true' : undefined"
      >
        Save details
      </button>
    </div>
  </form>
</template>
