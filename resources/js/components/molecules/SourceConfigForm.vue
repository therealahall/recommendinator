<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { SourceFieldSchema } from '@/types/api'

const props = withDefaults(
  defineProps<{
    schema: SourceFieldSchema[]
    values: Record<string, unknown>
    secretStatus: Record<string, boolean>
    saving?: boolean
    disabled?: boolean
  }>(),
  { saving: false, disabled: false },
)

const emit = defineEmits<{
  save: [values: Record<string, unknown>]
  'set-secret': [name: string, value: string]
  'clear-secret': [name: string]
}>()

type FormValue = string | number | boolean | string[]

const nonSensitiveFields = computed(() =>
  props.schema.filter((f) => !f.sensitive),
)

const sensitiveFields = computed(() => props.schema.filter((f) => f.sensitive))

function defaultFor(field: SourceFieldSchema): FormValue {
  if (field.field_type === 'bool') return false
  if (field.field_type === 'int' || field.field_type === 'float') return 0
  if (field.field_type === 'list') return []
  return ''
}

function coerce(field: SourceFieldSchema, raw: unknown): FormValue {
  if (raw === undefined || raw === null) return defaultFor(field)
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

const formValues = reactive<Record<string, FormValue>>({})

function syncFormFromProps(): void {
  for (const field of nonSensitiveFields.value) {
    formValues[field.name] = coerce(field, props.values[field.name])
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

function removeChip(name: string, index: number): void {
  const list = getList(name).filter((_, i) => i !== index)
  formValues[name] = list
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
  const out: Record<string, unknown> = {}
  for (const field of nonSensitiveFields.value) {
    out[field.name] = formValues[field.name]
  }
  emit('save', out)
}

const secretEditing = reactive<Record<string, boolean>>({})
const secretDrafts = reactive<Record<string, string>>({})

function startReplace(name: string): void {
  secretEditing[name] = true
  secretDrafts[name] = ''
}

function cancelReplace(name: string): void {
  secretEditing[name] = false
  secretDrafts[name] = ''
}

function saveSecret(name: string): void {
  const value = secretDrafts[name] ?? ''
  if (!value) return
  emit('set-secret', name, value)
  secretEditing[name] = false
  secretDrafts[name] = ''
}

function clearSecret(name: string): void {
  emit('clear-secret', name)
}

function isSecretSet(name: string): boolean {
  return Boolean(props.secretStatus[name])
}
</script>

<template>
  <form class="source-form" @submit.prevent="onSave">
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
        :disabled="disabled"
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
        :disabled="disabled"
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
        :disabled="disabled"
        :value="formValues[field.name] as number"
        @input="onFloatInput(field.name, ($event.target as HTMLInputElement).value)"
      />

      <input
        v-else-if="field.field_type === 'bool'"
        :id="`field-${field.name}`"
        :name="field.name"
        type="checkbox"
        :required="field.required"
        :disabled="disabled"
        :checked="formValues[field.name] as boolean"
        @change="formValues[field.name] = ($event.target as HTMLInputElement).checked"
      />

      <div v-else-if="field.field_type === 'list'" class="chips-container">
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
            :disabled="disabled"
            @click="removeChip(field.name, index)"
          >×</button>
        </span>
        <input
          :id="`field-${field.name}`"
          type="text"
          class="chip-input"
          :data-testid="`chip-input-${field.name}`"
          :placeholder="`Add ${field.name}…`"
          :disabled="disabled"
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
            {{ isSecretSet(field.name) ? 'set' : 'unset' }}
          </span>
          <button
            v-if="!secretEditing[field.name]"
            type="button"
            class="btn btn-secondary"
            :data-testid="`secret-replace-${field.name}`"
            :disabled="disabled"
            @click="startReplace(field.name)"
          >Replace</button>
          <button
            v-if="!secretEditing[field.name] && isSecretSet(field.name)"
            type="button"
            class="btn btn-danger"
            :data-testid="`secret-clear-${field.name}`"
            :disabled="disabled"
            @click="clearSecret(field.name)"
          >Clear</button>
        </div>
        <p v-if="field.description" class="source-form-help">
          {{ field.description }}
        </p>
        <div v-if="secretEditing[field.name]" class="secret-edit-row">
          <input
            :id="`secret-input-${field.name}`"
            :name="field.name"
            type="password"
            autocomplete="new-password"
            :aria-label="`New value for ${field.name}`"
            :value="secretDrafts[field.name] ?? ''"
            @input="
              secretDrafts[field.name] = ($event.target as HTMLInputElement).value
            "
          />
          <button
            type="button"
            class="btn btn-primary"
            :data-testid="`secret-save-${field.name}`"
            :disabled="disabled"
            @click="saveSecret(field.name)"
          >Save secret</button>
          <button
            type="button"
            class="btn btn-secondary"
            :data-testid="`secret-cancel-${field.name}`"
            @click="cancelReplace(field.name)"
          >Cancel</button>
        </div>
      </div>
    </fieldset>

    <div class="source-form-actions">
      <button
        type="button"
        class="btn btn-primary"
        data-testid="form-save"
        :disabled="saving || disabled"
        @click="onSave"
      >{{ saving ? 'Saving…' : 'Save' }}</button>
    </div>
  </form>
</template>

<style scoped>
.source-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.source-form-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.source-form-label {
  font-weight: 600;
  font-size: var(--text-sm);
}

.source-form-help {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: 0;
}

.source-form-secrets {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.source-form-secrets legend {
  padding: 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.secret-status-row,
.secret-edit-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.secret-status-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--surface-alt, rgba(0, 0, 0, 0.06));
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
}

.chips-container {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  align-items: center;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: var(--surface-alt, rgba(0, 0, 0, 0.06));
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
  flex: 1 1 8rem;
  min-width: 8rem;
}

.source-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
