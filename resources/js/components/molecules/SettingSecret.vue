<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { SettingViewSecret } from '@/types/api'

const props = withDefaults(
  defineProps<{
    setting: SettingViewSecret
    disabled?: boolean
    busy?: boolean
  }>(),
  { disabled: false, busy: false },
)

const emit = defineEmits<{
  set: [value: string]
  clear: []
}>()

const editing = ref(false)
const draft = ref('')
const draftInput = ref<HTMLInputElement | null>(null)
const replaceButton = ref<HTMLButtonElement | null>(null)

// Each action removes the just-clicked control from the DOM, so move focus to
// the control that replaces it rather than letting it drop to <body> (WCAG 2.4.3).
async function startReplace(): Promise<void> {
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

function save(): void {
  const value = draft.value
  if (!value) return
  emit('set', value)
  editing.value = false
  draft.value = ''
}

// A Save or Clear round-trips through the parent, which disables the Replace/Set
// button (busy) for the request's duration; focusing it while disabled is a
// silent no-op. Wait for busy to fall back to false, then return focus to the
// button that replaced the just-clicked control (WCAG 2.4.3).
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
        :disabled="disabled || busy"
        @click="startReplace"
      >{{ setting.has_secret ? 'Replace' : 'Set' }}</button>
      <button
        v-if="!editing && setting.has_secret"
        type="button"
        class="btn btn-danger"
        :aria-label="`Clear ${setting.label}`"
        :data-testid="`secret-clear-${setting.key}`"
        :disabled="disabled || busy"
        @click="emit('clear')"
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
        :disabled="busy"
        @input="draft = ($event.target as HTMLInputElement).value"
      />
      <button
        type="button"
        class="btn btn-primary"
        :aria-label="`Save ${setting.label}`"
        :data-testid="`secret-save-${setting.key}`"
        :disabled="busy"
        @click="save"
      >Save secret</button>
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
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.secret-edit-row input[type='password']:hover {
  border-color: var(--accent);
}

.secret-edit-row input[type='password']:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
}
</style>
