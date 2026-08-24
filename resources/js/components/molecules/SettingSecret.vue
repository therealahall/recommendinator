<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
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
  if (!value || locked()) return
  emit('set', value)
  editing.value = false
  draft.value = ''
  await nextTick()
  replaceButton.value?.focus()
}

// Clear unmounts its own button once the secret is gone, and only then.
watch(
  () => props.busy,
  (busy, wasBusy) => {
    if (wasBusy && !busy) nextTick(() => replaceButton.value?.focus())
  },
)
</script>

<template>
  <div class="source-form-field">
    <div class="secret-status-row">
      <span class="source-form-label">{{ setting.label }}</span>
      <span class="secret-status-badge" :data-testid="`secret-status-${setting.key}`">
        {{ setting.has_secret ? 'Set' : 'Not set' }}
      </span>
      <button
        v-if="!editing"
        ref="replaceButton"
        type="button"
        class="btn btn-secondary"
        :aria-label="`${setting.has_secret ? 'Replace' : 'Set'} ${setting.label}`"
        :data-testid="`secret-replace-${setting.key}`"
        :aria-disabled="verbsLocked || busy || undefined"
        @click="startReplace"
      >{{ setting.has_secret ? 'Replace' : 'Set' }}</button>
      <button
        v-if="!editing && setting.has_secret"
        type="button"
        class="btn btn-danger"
        :aria-label="`Clear ${setting.label}`"
        :data-testid="`secret-clear-${setting.key}`"
        :aria-disabled="verbsLocked || busy || undefined"
        @click="clear"
      >Clear</button>
    </div>

    <p v-if="setting.help" class="source-form-help">{{ setting.help }}</p>

    <div v-if="editing" class="secret-edit-row">
      <input
        :id="`secret-input-${setting.key}`"
        ref="draftInput"
        type="password"
        autocomplete="new-password"
        :aria-label="`New value for ${setting.label}`"
        :value="draft"
        :readonly="verbsLocked || busy"
        @input="draft = ($event.target as HTMLInputElement).value"
      />
      <button
        type="button"
        class="btn btn-primary"
        :aria-label="`Save ${setting.label}`"
        :data-testid="`secret-save-${setting.key}`"
        :aria-disabled="verbsLocked || busy || undefined"
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
/* .source-form-field/-label/-help and .secret-status-row/.secret-edit-row are
   shared primitives defined in base.css. */
.secret-status-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  /* --text-primary on the tinted surface keeps the badge legible; the "Set" /
     "Not set" text conveys the state without relying on colour. */
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-secondary) 12%, transparent);
  padding: 2px var(--space-2);
  border-radius: 999px;
}

.secret-edit-row input[type='password'] {
  flex: 1;
  min-width: 12rem;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
  transition: border-color 0.15s ease;
}

.secret-edit-row input[type='password']:hover:not([readonly]) {
  border-color: var(--accent);
}

.secret-edit-row input[readonly] {
  border-style: dashed;
  background: var(--bg-elevated);
  cursor: not-allowed;
}
</style>
