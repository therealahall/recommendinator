<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { rescueFocus } from '@/utils/focus'
import type { SettingViewSecret } from '@/types/api'

const props = withDefaults(
  defineProps<{
    setting: SettingViewSecret
    /** Locks the verbs while the section around them saves; Cancel stays open. */
    verbsLocked?: boolean
    busy?: boolean
  }>(),
  { verbsLocked: false, busy: false },
)

const emit = defineEmits<{
  set: [value: string]
  clear: []
}>()

const editing = ref(false)
const draft = ref('')
const draftInput = ref<HTMLInputElement | null>(null)
const replaceButton = ref<HTMLButtonElement | null>(null)

const locked = (): boolean => props.verbsLocked || props.busy
const saveBlocked = (): boolean => locked() || draft.value === ''

// Each verb removes the control that was clicked, so focus is placed on what
// replaced it (WCAG 2.4.3). aria-disabled still activates, so each verb guards.
async function startReplace(): Promise<void> {
  if (locked()) return
  editing.value = true
  draft.value = ''
  await nextTick()
  draftInput.value?.focus()
}

async function cancel(): Promise<void> {
  editing.value = false
  draft.value = ''
  await nextTick()
  replaceButton.value?.focus()
}

function clear(): void {
  if (locked()) return
  emit('clear')
}

async function save(): Promise<void> {
  const value = draft.value
  if (saveBlocked()) return
  emit('set', value)
  editing.value = false
  draft.value = ''
  await nextTick()
  replaceButton.value?.focus()
}

// Clear unmounts its own button, stranding focus; a Tab away keeps its place.
watch(
  () => props.busy,
  (busy, wasBusy) => {
    if (!wasBusy || busy) return
    nextTick(() => rescueFocus(replaceButton.value))
  },
)
</script>

<template>
  <div class="source-form-field">
    <div class="secret-status-row">
      <span class="source-form-label">{{ setting.label }}</span>
      <span class="badge" :data-testid="`secret-status-${setting.key}`">
        {{ setting.has_secret ? 'Set' : 'Not set' }}
      </span>
      <button
        v-if="!editing"
        ref="replaceButton"
        type="button"
        class="btn btn-secondary"
        :aria-label="`${setting.has_secret ? 'Replace' : 'Set'} ${setting.label}`"
        :data-testid="`secret-replace-${setting.key}`"
        :aria-disabled="locked() || undefined"
        @click="startReplace"
      >{{ setting.has_secret ? 'Replace' : 'Set' }}</button>
      <button
        v-if="!editing && setting.has_secret"
        type="button"
        class="btn btn-danger"
        :aria-label="`Clear ${setting.label}`"
        :data-testid="`secret-clear-${setting.key}`"
        :aria-disabled="locked() || undefined"
        @click="clear"
      >Clear</button>
    </div>

    <p v-if="setting.help" class="source-form-help">{{ setting.help }}</p>

    <div v-if="editing" class="secret-edit-row">
      <input
        :id="`secret-input-${setting.key}`"
        ref="draftInput"
        type="password"
        class="field"
        autocomplete="new-password"
        :aria-label="`New value for ${setting.label}`"
        :value="draft"
        :readonly="locked()"
        @input="draft = ($event.target as HTMLInputElement).value"
      />
      <button
        type="button"
        class="btn btn-primary"
        :aria-label="`Save ${setting.label}`"
        :data-testid="`secret-save-${setting.key}`"
        :aria-disabled="saveBlocked() || undefined"
        @click="save"
      >Save secret</button>
      <!-- Not locked: it is the only way out of the edit row. -->
      <button
        type="button"
        class="btn btn-secondary"
        :aria-label="`Cancel replacing ${setting.label}`"
        :data-testid="`secret-cancel-${setting.key}`"
        @click="cancel"
      >Cancel</button>
    </div>
  </div>
</template>

<style scoped>
/* .badge, .field, .source-form-field/-label/-help and
   .secret-status-row/.secret-edit-row are shared primitives in base.css. */
.secret-edit-row .field {
  flex: 1;
  min-width: min(12rem, 100%);
}
</style>
