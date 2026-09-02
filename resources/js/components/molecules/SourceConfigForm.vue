<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import { rescueFocus } from '@/utils/focus'
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

const saveAnnouncement = computed(() =>
  props.saveStatus === 'saved' ? `${props.sourceName} settings saved.` : '',
)

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
  rescueFocus(control(`secret-replace-${name}`))
}

watch(
  () => ({ ...props.secretSave }),
  (now, before) => {
    for (const [name, status] of Object.entries(now)) {
      if (status === before?.[name]) continue
      if (status === 'error') void nextTick(() => rescueFocus(control(`secret-error-${name}`)))
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
        class="field"
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
        class="field"
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
        class="field"
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

      <div v-else-if="field.field_type === 'list'" class="chips-field field">
        <div
          v-if="getList(field.name).length > 0"
          class="chips-list"
        >
          <span
            v-for="(chip, index) in getList(field.name)"
            :key="`${field.name}-${index}`"
            data-testid="chip"
            class="badge"
            data-tone="accent"
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
          <span class="badge" :data-testid="`secret-status-${field.name}`">
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
            :class="{ badge: secretSaveStatus(field.name) === 'saved' }"
            :data-tone="secretSaveStatus(field.name) === 'saved' ? 'success' : undefined"
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
            class="field"
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
          class="state state--error secret-error focus-fallback"
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
        @click="onToggleEnabled"
      >{{
        enableBusy
          ? (enabled ? 'Disabling…' : 'Enabling…')
          : (enabled ? 'Disable' : 'Enable')
      }}</button>
      <!-- Deliberately NOT a live region: the error span below is one already,
           and aria-atomic here would drag the button label into every
           announcement. The saved pill is visible text; the region below the
           actions speaks for it. -->
      <div class="source-form-save-group">
        <span
          v-if="saveStatus === 'saved'"
          class="badge"
          data-tone="success"
          data-testid="form-save-status"
        >Saved ✓</span>
        <span
          v-else-if="saveStatus === 'error'"
          class="badge badge--wrap"
          data-tone="error"
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

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3),
         and nothing else says the write landed — focus stays on Save. -->
    <p
      class="sr-only"
      data-testid="form-save-announcement"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ saveAnnouncement }}</p>
  </form>
</template>

<style scoped>
.source-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.source-form-field .field {
  width: 100%;
}

/* Padding would leave a strip of --bg-input around the flush rows, and clipping
   the overflow would take the entry's outward focus ring with it. */
.chips-field {
  display: flex;
  flex-direction: column;
  padding: 0;
  transition: border-color 0.15s ease;
}

.chips-field:focus-within {
  border-color: var(--border-focus);
}

.chips-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border-default);
}

.chip-remove {
  align-items: center;
  background: transparent;
  border: 0;
  cursor: pointer;
  display: inline-flex;
  font-size: 1.1em;
  justify-content: center;
  line-height: 1;
  min-height: 24px;
  min-width: 24px;
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
  /* Mouse focus stays subtle — the parent's :focus-within edge already says
     where focus is. */
  outline: none;
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

.secret-error:not(:empty) {
  margin-top: var(--space-2);
}

/* Enable stays distinct from Save without leaning on btn-primary. It mixes
   toward black under a white label, as btn-danger does: --bg-primary on the raw
   success colour is a light label on a light fill in a light theme. */
:deep(.btn-success) {
  background: color-mix(in srgb, var(--color-success) 60%, black);
  color: var(--text-on-dark-fill);
  border-color: color-mix(in srgb, var(--color-success) 60%, black);
  font-weight: var(--weight-semibold);
}

:deep(.btn-success:hover:not(:disabled):not([aria-disabled='true'])) {
  background: color-mix(in srgb, var(--color-success) 50%, black);
  border-color: color-mix(in srgb, var(--color-success) 50%, black);
}
</style>
