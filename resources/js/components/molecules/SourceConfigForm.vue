<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import { focusStranded } from '@/utils/focus'
import type { SourceFieldSchema } from '@/types/api'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

const props = withDefaults(
  defineProps<{
    schema: SourceFieldSchema[]
    values: Record<string, unknown>
    secretStatus: Record<string, boolean>
    sourceName: string
    saving?: boolean
    /** Locks the secret and enable verbs, never the fields: each refetches the
     *  config, and syncFormFromProps then wipes an edit typed but not saved. */
    verbsLocked?: boolean
    enabled?: boolean | null
    enableBusy?: boolean
    saveStatus?: SaveStatus
    saveError?: string
    secretSave?: Record<string, SaveStatus>
    secretSaveError?: Record<string, string>
  }>(),
  {
    saving: false,
    verbsLocked: false,
    enabled: null,
    enableBusy: false,
    saveStatus: 'idle',
    saveError: '',
    secretSave: () => ({}),
    secretSaveError: () => ({}),
  },
)

const emit = defineEmits<{
  save: [values: Record<string, unknown>]
  'set-secret': [name: string, value: string]
  'clear-secret': [name: string]
  'toggle-enabled': [next: boolean]
}>()

type FormValue = string | number | boolean | string[]

const nonSensitiveFields = computed(() =>
  props.schema.filter((f) => !f.sensitive),
)

const sensitiveFields = computed(() => props.schema.filter((f) => f.sensitive))

function zeroFor(field: SourceFieldSchema): FormValue {
  if (field.field_type === 'bool') return false
  if (field.field_type === 'int' || field.field_type === 'float') return 0
  if (field.field_type === 'list') return []
  return ''
}

function asFormValue(field: SourceFieldSchema, raw: unknown): FormValue {
  if (raw === undefined || raw === null) return zeroFor(field)
  if (field.field_type === 'bool') return Boolean(raw)
  if (field.field_type === 'int') {
    const n = typeof raw === 'number' ? raw : parseInt(String(raw), 10)
    return Number.isFinite(n) ? n : 0
  }
  if (field.field_type === 'float') {
    const n = typeof raw === 'number' ? raw : parseFloat(String(raw))
    return Number.isFinite(n) ? n : 0
  }
  if (field.field_type === 'list') {
    return Array.isArray(raw) ? raw.map(String) : []
  }
  return String(raw)
}

function sameValue(a: FormValue, b: FormValue): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, index) => item === b[index])
  }
  return a === b
}

function defaultValue(field: SourceFieldSchema): FormValue {
  return asFormValue(field, field.default)
}

function hasStoredValue(field: SourceFieldSchema): boolean {
  const stored = props.values[field.name]
  return stored !== undefined && stored !== null
}

function isUnstoredDefault(field: SourceFieldSchema): boolean {
  return (
    !hasStoredValue(field) &&
    sameValue(formValues[field.name], defaultValue(field))
  )
}

const formValues = reactive<Record<string, FormValue>>({})

function syncFormFromProps(): void {
  for (const field of nonSensitiveFields.value) {
    formValues[field.name] = hasStoredValue(field)
      ? asFormValue(field, props.values[field.name])
      : defaultValue(field)
  }
}

watch(
  () => [props.schema, props.values] as const,
  () => syncFormFromProps(),
  { immediate: true, deep: true },
)

const chipDrafts = reactive<Record<string, string>>({})

function getList(name: string): string[] {
  const value = formValues[name]
  return Array.isArray(value) ? (value as string[]) : []
}

function addChip(name: string): void {
  const draft = (chipDrafts[name] ?? '').trim()
  if (!draft) return
  const list = [...getList(name), draft]
  formValues[name] = list
  chipDrafts[name] = ''
}

const formRoot = ref<HTMLFormElement | null>(null)

async function removeChip(name: string, index: number): Promise<void> {
  const list = getList(name).filter((_, i) => i !== index)
  formValues[name] = list
  // The × button the user just pressed unmounts with its chip, dropping focus
  // to <body> (WCAG 2.4.3). Land on the chip that shifted into this slot, or
  // the draft input when the last one goes.
  await nextTick()
  const target = list.length
    ? formRoot.value?.querySelector<HTMLElement>(
        `[data-testid="chip-remove-${name}-${Math.min(index, list.length - 1)}"]`,
      )
    : undefined
  ;(
    target ??
    formRoot.value?.querySelector<HTMLElement>(`[data-testid="chip-input-${name}"]`)
  )?.focus()
}

function onIntInput(name: string, raw: string): void {
  const n = parseInt(raw, 10)
  formValues[name] = Number.isFinite(n) ? n : 0
}

function onFloatInput(name: string, raw: string): void {
  const n = parseFloat(raw)
  formValues[name] = Number.isFinite(n) ? n : 0
}

function onSave(): void {
  if (props.saving) return
  const out: Record<string, unknown> = {}
  for (const field of nonSensitiveFields.value) {
    // The API merges, so an omitted field stays unset — which is what an
    // untouched default means. Writing it back freezes today's default as a
    // stored value, and for a list that value reads as "none".
    if (isUnstoredDefault(field)) continue
    out[field.name] = formValues[field.name]
  }
  emit('save', out)
}

// aria-disabled, which still activates, so every locked verb guards.
function onToggleEnabled(): void {
  if (props.enableBusy || props.verbsLocked) return
  emit('toggle-enabled', !props.enabled)
}

const secretEditing = reactive<Record<string, boolean>>({})
const secretDrafts = reactive<Record<string, string>>({})
const clearConfirming = ref('')

function control(testid: string): HTMLElement | null {
  return formRoot.value?.querySelector<HTMLElement>(`[data-testid="${testid}"]`) ?? null
}

function secretSaveStatus(name: string): SaveStatus {
  return props.secretSave[name] ?? 'idle'
}

function secretBusy(name: string): boolean {
  return secretSaveStatus(name) === 'saving'
}

function saveSecretBlocked(name: string): boolean {
  return props.verbsLocked || secretBusy(name) || (secretDrafts[name] ?? '') === ''
}

// Each verb unmounts the control clicked, so focus lands on what replaced it (WCAG 2.4.3).
async function startReplace(name: string): Promise<void> {
  if (props.verbsLocked || secretBusy(name)) return
  secretEditing[name] = true
  secretDrafts[name] = ''
  await nextTick()
  control(`secret-input-${name}`)?.focus()
}

async function cancelReplace(name: string): Promise<void> {
  secretEditing[name] = false
  secretDrafts[name] = ''
  await nextTick()
  control(`secret-replace-${name}`)?.focus()
}

// The row stays open: collapsing on the emit reported a stored key and a
// refused one identically.
function saveSecret(name: string): void {
  if (saveSecretBlocked(name)) return
  emit('set-secret', name, secretDrafts[name] ?? '')
}

function askClear(name: string): void {
  if (props.verbsLocked || secretBusy(name)) return
  clearConfirming.value = name
}

async function cancelClear(name: string): Promise<void> {
  clearConfirming.value = ''
  await nextTick()
  control(`secret-clear-${name}`)?.focus()
}

function confirmClear(name: string): void {
  clearConfirming.value = ''
  emit('clear-secret', name)
}

// Closing the row strands focus on <body>; a Tab away mid-write keeps its place.
async function settleSecret(name: string): Promise<void> {
  secretEditing[name] = false
  secretDrafts[name] = ''
  await nextTick()
  if (focusStranded()) control(`secret-replace-${name}`)?.focus()
}

watch(
  () => ({ ...props.secretSave }),
  (now, before) => {
    for (const [name, status] of Object.entries(now)) {
      if (status === before?.[name]) continue
      // A refusal takes focus, so it is read where it happened.
      if (status === 'error') void nextTick(() => control(`secret-error-${name}`)?.focus())
      else if (status === 'saved') void settleSecret(name)
    }
  },
)

function isSecretSet(name: string): boolean {
  return Boolean(props.secretStatus[name])
}
</script>

<template>
  <form ref="formRoot" class="source-form" @submit.prevent="onSave">
    <div
      v-for="field in nonSensitiveFields"
      :key="field.name"
      class="source-form-field"
    >
      <label :for="`field-${field.name}`" class="source-form-label">
        {{ field.name }}
        <span v-if="field.required" aria-hidden="true">*</span>
      </label>

      <input
        v-if="field.field_type === 'str'"
        :id="`field-${field.name}`"
        :name="field.name"
        type="text"
        :required="field.required"
        :value="formValues[field.name] as string"
        @input="formValues[field.name] = ($event.target as HTMLInputElement).value"
      />

      <input
        v-else-if="field.field_type === 'int'"
        :id="`field-${field.name}`"
        :name="field.name"
        type="number"
        step="1"
        :required="field.required"
        :value="formValues[field.name] as number"
        @input="onIntInput(field.name, ($event.target as HTMLInputElement).value)"
      />

      <input
        v-else-if="field.field_type === 'float'"
        :id="`field-${field.name}`"
        :name="field.name"
        type="number"
        step="any"
        :required="field.required"
        :value="formValues[field.name] as number"
        @input="onFloatInput(field.name, ($event.target as HTMLInputElement).value)"
      />

      <input
        v-else-if="field.field_type === 'bool'"
        :id="`field-${field.name}`"
        :name="field.name"
        type="checkbox"
        :required="field.required"
        :checked="formValues[field.name] as boolean"
        @change="formValues[field.name] = ($event.target as HTMLInputElement).checked"
      />

      <div v-else-if="field.field_type === 'list'" class="chips-field">
        <div
          v-if="getList(field.name).length > 0"
          class="chips-list"
        >
          <span
            v-for="(chip, index) in getList(field.name)"
            :key="`${field.name}-${index}`"
            data-testid="chip"
            class="chip"
          >
            {{ chip }}
            <button
              type="button"
              class="chip-remove"
              :data-testid="`chip-remove-${field.name}-${index}`"
              :aria-label="`Remove ${chip}`"
              @click="removeChip(field.name, index)"
            >×</button>
          </span>
        </div>
        <input
          :id="`field-${field.name}`"
          type="text"
          class="chip-input"
          :data-testid="`chip-input-${field.name}`"
          :placeholder="`Add ${field.name}…`"
          :value="chipDrafts[field.name] ?? ''"
          @input="chipDrafts[field.name] = ($event.target as HTMLInputElement).value"
          @keydown.enter.prevent="addChip(field.name)"
        />
      </div>

      <p v-if="field.description" class="source-form-help">
        {{ field.description }}
      </p>
    </div>

    <fieldset
      v-if="sensitiveFields.length > 0"
      class="source-form-secrets"
    >
      <legend>Secrets</legend>
      <div
        v-for="field in sensitiveFields"
        :key="field.name"
        class="source-form-field"
      >
        <div class="secret-status-row">
          <span class="source-form-label">{{ field.name }}</span>
          <span class="secret-status-badge">
            <span class="sr-only">{{ field.name }} secret is</span>
            {{ isSecretSet(field.name) ? 'set' : 'unset' }}
          </span>
          <button
            v-if="!secretEditing[field.name]"
            type="button"
            class="btn btn-secondary"
            :aria-label="`Replace ${field.name}`"
            :data-testid="`secret-replace-${field.name}`"
            :aria-disabled="verbsLocked || secretBusy(field.name) || undefined"
            @click="startReplace(field.name)"
          >Replace</button>
          <button
            v-if="!secretEditing[field.name] && isSecretSet(field.name)"
            type="button"
            class="btn btn-danger"
            :aria-label="`Clear ${field.name}`"
            :data-testid="`secret-clear-${field.name}`"
            :aria-disabled="verbsLocked || secretBusy(field.name) || undefined"
            @click="askClear(field.name)"
          >Clear</button>
          <!-- Mounted while silent: inserted populated it reads as content
               (4.1.3), and nothing else announces that the write landed —
               focus goes to Replace, whose name does not change. -->
          <span
            class="secret-saved"
            :class="{
              'source-form-save-status source-form-save-status--ok':
                secretSaveStatus(field.name) === 'saved',
            }"
            :data-testid="`secret-saved-${field.name}`"
            role="status"
          >{{
            secretSaveStatus(field.name) === 'saved'
              ? `${field.name} ${isSecretSet(field.name) ? 'saved' : 'cleared'}`
              : ''
          }}</span>
        </div>
        <p v-if="field.description" class="source-form-help">
          {{ field.description }}
        </p>

        <ConfirmPanel
          v-if="clearConfirming === field.name"
          :message="`Clear ${field.name} for ${sourceName}? The stored value is deleted for good — getting it back means fetching it from the provider again.`"
          confirm-label="Clear"
          cancel-label="Keep it"
          destructive
          @cancel="cancelClear(field.name)"
          @confirm="confirmClear(field.name)"
        />

        <div v-if="secretEditing[field.name]" class="secret-edit-row">
          <input
            :id="`secret-input-${field.name}`"
            :name="field.name"
            type="password"
            autocomplete="new-password"
            :aria-label="`New value for ${field.name}`"
            :data-testid="`secret-input-${field.name}`"
            :value="secretDrafts[field.name] ?? ''"
            :readonly="verbsLocked || secretBusy(field.name)"
            @input="
              secretDrafts[field.name] = ($event.target as HTMLInputElement).value
            "
          />
          <button
            type="button"
            class="btn btn-primary"
            :aria-label="`Save ${field.name}`"
            :data-testid="`secret-save-${field.name}`"
            :aria-disabled="saveSecretBlocked(field.name) || undefined"
            @click="saveSecret(field.name)"
          >{{ secretBusy(field.name) ? 'Saving…' : 'Save secret' }}</button>
          <!-- Not locked: it is the only way out of the edit row. -->
          <button
            type="button"
            class="btn btn-secondary"
            :aria-label="`Cancel replacing ${field.name}`"
            :data-testid="`secret-cancel-${field.name}`"
            @click="cancelReplace(field.name)"
          >Cancel</button>
        </div>

        <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
        <p
          class="secret-error focus-fallback"
          :data-testid="`secret-error-${field.name}`"
          role="alert"
          tabindex="-1"
        >{{
          secretSaveStatus(field.name) === 'error'
            ? `Error: ${secretSaveError[field.name] || 'failed to save'}`
            : ''
        }}</p>
      </div>
    </fieldset>

    <div class="source-form-actions">
      <slot name="actions-extra" />
      <button
        v-if="enabled !== null"
        type="button"
        class="btn source-form-toggle-btn"
        :class="enabled ? 'btn-danger' : 'btn-success'"
        data-testid="form-toggle-enabled"
        :aria-disabled="enableBusy || verbsLocked || undefined"
        :aria-pressed="enabled"
        @click="onToggleEnabled"
      >{{
        enableBusy
          ? (enabled ? 'Disabling…' : 'Enabling…')
          : (enabled ? 'Disable' : 'Enable')
      }}</button>
      <!-- Deliberately NOT a live region: the spans below carry role="status"/
           role="alert", which are implicit live regions already. Nesting them
           inside another one double-announces, and aria-atomic here would drag
           the button label into every announcement. -->
      <div class="source-form-save-group">
        <span
          v-if="saveStatus === 'saved'"
          class="source-form-save-status source-form-save-status--ok"
          data-testid="form-save-status"
          role="status"
        >Saved ✓</span>
        <span
          v-else-if="saveStatus === 'error'"
          class="source-form-save-status source-form-save-status--err"
          data-testid="form-save-status"
          role="alert"
        >Error: {{ saveError || 'failed to save' }}</span>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="form-save"
          :aria-disabled="saving || undefined"
          @click="onSave"
        >{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </div>
  </form>
</template>

<style scoped>
.source-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.source-form-field input[type="text"],
.source-form-field input[type="number"],
.source-form-field input[type="password"] {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-card);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
  transition: border-color 0.15s ease;
}

.source-form-field input[type="text"]:hover,
.source-form-field input[type="number"]:hover,
.source-form-field input[type="password"]:hover:not([readonly]) {
  border-color: var(--accent);
}

/* On the edge and the fill, never a fade: a `readonly` control is not exempt
   from 4.5:1 the way the `disabled` it replaced was (WCAG 1.4.3). */
.source-form-field input[readonly] {
  border-style: dashed;
  background: var(--bg-elevated);
  cursor: not-allowed;
}

.secret-status-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--bg-elevated);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
}

.chips-field {
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-sm);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.chips-field:hover {
  border-color: var(--accent);
}

.chips-field:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
}

.chips-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border-default);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: color-mix(in srgb, var(--accent) 25%, transparent);
  color: var(--text-primary);
  padding: 0 var(--space-2);
  border-radius: 999px;
  font-size: var(--text-sm);
}

.chip-remove {
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 1.1em;
  line-height: 1;
  padding: 0 0 0 var(--space-1);
  color: inherit;
}

.chip-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: 0;
  color: var(--text-primary);
  font: inherit;
}

.chip-input:focus:not(:focus-visible) {
  /* Mouse focus stays subtle — the .chips-field:focus-within halo on the
     parent already conveys focus position. */
  outline: none;
}

.chip-input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.chip-input::placeholder {
  color: var(--text-secondary);
  font-style: italic;
}

.source-form-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-default);
}

.source-form-save-group {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

/* Clipped rather than `display: none` while silent: the row must not shift
   when the write lands, but a region that leaves the accessibility tree and
   comes back populated is an insertion, which is the case 4.1.3 excludes. */
.secret-saved:empty {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border-width: 0;
}

.secret-error {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-error-text);
}

.secret-error:not(:empty) {
  margin-top: var(--space-2);
}

.source-form-save-status {
  font-size: var(--text-sm);
  font-weight: 500;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  animation: source-form-save-status-fade 0.2s ease-out;
}

/* Use --text-primary on a tinted background so the pill clears WCAG
   1.4.3 4.5:1 contrast at the small (13px) pill size. The semantic
   colour shows through the background tint and a leading icon. */
.source-form-save-status--ok {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-success) 35%, transparent);
}

/* Wraps where the "Saved ✓" pill does not: a refusal names the remedy, which
   runs to a sentence or three, and nowrap would run it off the form. */
.source-form-save-status--err {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
  white-space: normal;
}

@keyframes source-form-save-status-fade {
  from { opacity: 0; transform: translateY(-2px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .source-form-save-status {
    animation: none;
  }
}

/* btn-success keeps the Enable button visually distinct from Save
   (primary blue) without leaning on btn-primary. The Nord palette
   ships a light green (`--color-success`) — pair it with dark text
   (`--bg-primary`) so the contrast clears WCAG AA easily. */
:deep(.btn-success) {
  background: var(--color-success);
  color: var(--bg-primary);
  border-color: var(--color-success);
  font-weight: 600;
}

:deep(.btn-success:hover:not(:disabled):not([aria-disabled='true'])) {
  background: color-mix(in srgb, var(--color-success) 88%, black);
  border-color: color-mix(in srgb, var(--color-success) 88%, black);
}

:deep(.btn-success:focus-visible) {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
