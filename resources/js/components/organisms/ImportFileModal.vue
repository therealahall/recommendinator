<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useFocusTrap } from '@/composables/useFocusTrap'
import { ApiError } from '@/composables/useApi'
import { useDataStore } from '@/stores/data'
import { truncate } from '@/utils/format'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'
import type { ImportResultResponse, ImportSourceResponse } from '@/types/api'

const emit = defineEmits<{
  close: []
}>()

const data = useDataStore()
const modalContent = ref<HTMLElement | null>(null)
const doneButton = ref<HTMLButtonElement | null>(null)
useFocusTrap(modalContent, () => emit('close'))

const sourceName = ref<string>('')
const file = ref<File | null>(null)
const fieldValues = ref<Record<string, string>>({})
const submitting = ref(false)
const result = ref<ImportResultResponse | null>(null)
const resultError = ref('')

onMounted(async () => {
  try {
    await data.loadImportSources()
  } catch (err) {
    resultError.value =
      err instanceof Error
        ? `Couldn't load import sources: ${err.message}`
        : "Couldn't load import sources."
    return
  }
  if (data.importSources.length > 0 && !sourceName.value) {
    sourceName.value = data.importSources[0].name
  }
})

const selectedSource = computed<ImportSourceResponse | undefined>(() =>
  data.importSources.find((s) => s.name === sourceName.value),
)

const visibleFields = computed(() =>
  selectedSource.value
    ? selectedSource.value.fields.filter((f) => !f.sensitive)
    : [],
)

// Re-seed option defaults and clear any stale file/banner whenever the user
// picks a different source — the accepted file types and option schema both
// change with the source.
watch(sourceName, () => {
  const next: Record<string, string> = {}
  for (const field of visibleFields.value) {
    next[field.name] = field.default != null ? String(field.default) : ''
  }
  fieldValues.value = next
  file.value = null
  result.value = null
  resultError.value = ''
})

// When the result banner arrives it swaps the Cancel/Import buttons for a
// single Done button, so the focused Import button leaves the DOM and focus
// would fall to <body>, escaping the trap. Move focus to Done (or the dialog
// container as a fallback) to keep keyboard users inside the modal (WCAG 2.4.3).
watch(result, (value) => {
  if (!value) return
  void nextTick(() => {
    ;(doneButton.value ?? modalContent.value)?.focus()
  })
})

const contentTypeOptions = computed(() => {
  const allowed = new Set(selectedSource.value?.content_types ?? [])
  return CONTENT_TYPE_OPTIONS.filter(
    (option) => option.value !== '' && allowed.has(option.value),
  )
})

// The four controlled import plugins encode their format in the plugin name
// (csv_import, json_import, markdown_import, goodreads → CSV), so we infer the
// accepted file types from the name rather than widening the API contract.
const importFormat = computed<'csv' | 'json' | 'markdown'>(() => {
  const name = selectedSource.value?.name ?? ''
  if (name.includes('json')) return 'json'
  if (name.includes('markdown')) return 'markdown'
  return 'csv'
})

const acceptAttr = computed(() => {
  switch (importFormat.value) {
    case 'json':
      return '.json,application/json'
    case 'markdown':
      return '.md,.markdown,text/markdown'
    default:
      return '.csv,text/csv'
  }
})

const acceptedExtensionsText = computed(() => {
  switch (importFormat.value) {
    case 'json':
      return 'Accepted file type: .json'
    case 'markdown':
      return 'Accepted file types: .md, .markdown'
    default:
      return 'Accepted file type: .csv'
  }
})

const requiredFilled = computed(() =>
  visibleFields.value.every(
    (field) =>
      !field.required || (fieldValues.value[field.name] ?? '') !== '',
  ),
)

// Conservative client-side gate: while ANY sync or import job is running we
// disable Import to serialize all imports for a simpler UX. This is broader
// than the server, which only returns 409 when an import with this same label
// is already running — we intentionally block more to avoid surprising the user.
const anyJobRunning = computed(() => data.syncStatus === 'running')

const canSubmit = computed(
  () =>
    !!file.value &&
    requiredFilled.value &&
    !submitting.value &&
    !anyJobRunning.value,
)

const disabledReason = computed(() => {
  if (!file.value) return 'Choose a file to import.'
  if (!requiredFilled.value) return 'Fill in all required fields.'
  if (anyJobRunning.value) {
    return 'Wait for the running job to finish, then try again.'
  }
  return ''
})

const importJob = computed(() =>
  selectedSource.value
    ? data.jobForLabel(`Import: ${selectedSource.value.display_name}`)
    : null,
)

const progressLabel = computed(() => {
  const job = importJob.value
  if (!job) return ''
  if (job.total_items != null && job.total_items > 0) {
    const pct =
      job.progress_percent != null ? ` (${job.progress_percent}%)` : ''
    return `${job.items_processed}/${job.total_items}${pct}`
  }
  return `${job.items_processed} items so far`
})

const successCounts = computed(() =>
  result.value
    ? `Imported ${result.value.items_synced} of ${result.value.total_items} items.`
    : '',
)

const skippedCount = computed(() => result.value?.errors.length ?? 0)
const skippedSummary = computed(
  () => `${skippedCount.value} row${skippedCount.value === 1 ? '' : 's'} skipped`,
)

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  file.value = input.files && input.files.length > 0 ? input.files[0] : null
}

function messageForStatus(status: number): string {
  if (status === 422) {
    return "That import source isn't available. Pick another format."
  }
  if (status === 400) {
    return "We couldn't read that file. Check that it matches the selected format and try again."
  }
  if (status === 409) {
    return 'An import or sync is already running. Wait for it to finish, then try again.'
  }
  return 'Something went wrong during the import. Please try again.'
}

async function submit(): Promise<void> {
  const source = selectedSource.value
  if (!source || !file.value || !canSubmit.value) return
  resultError.value = ''
  result.value = null
  submitting.value = true
  const options: Record<string, string> = {}
  for (const field of visibleFields.value) {
    const value = fieldValues.value[field.name]
    if (value !== undefined && value !== '') options[field.name] = value
  }
  try {
    result.value = await data.runImport(source.name, file.value, options)
  } catch (err) {
    result.value = null
    resultError.value =
      err instanceof ApiError
        ? messageForStatus(err.status)
        : 'Something went wrong during the import. Please try again.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="import-modal" @click.self="emit('close')">
    <div
      ref="modalContent"
      class="import-modal-content"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-modal-title"
      tabindex="-1"
    >
      <h3 id="import-modal-title">Import from file</h3>
      <p class="help-text">
        Upload an export to add items in one shot. Supported formats are CSV,
        JSON, and Markdown, plus Goodreads CSV exports.
      </p>

      <div class="import-modal-field">
        <label for="import-source">Source</label>
        <select
          id="import-source"
          v-model="sourceName"
          :disabled="submitting"
          :aria-describedby="selectedSource ? 'import-source-desc' : undefined"
        >
          <option
            v-for="source in data.importSources"
            :key="source.name"
            :value="source.name"
          >{{ source.display_name }}</option>
        </select>
        <p v-if="selectedSource" id="import-source-desc" class="help-text">
          {{ selectedSource.description }}
        </p>
      </div>

      <div class="import-modal-field">
        <label for="import-file">File</label>
        <input
          id="import-file"
          type="file"
          :accept="acceptAttr"
          :disabled="submitting"
          aria-describedby="import-file-accepted"
          @change="onFileChange"
        />
        <p id="import-file-accepted" class="help-text">{{ acceptedExtensionsText }}</p>
        <p v-if="file" class="help-text">Selected file: {{ file.name }}</p>
      </div>

      <div
        v-for="field in visibleFields"
        :key="field.name"
        class="import-modal-field"
      >
        <label :for="`import-field-${field.name}`">
          {{ field.name }}
          <span v-if="field.required" aria-hidden="true">*</span>
        </label>
        <select
          v-if="field.name === 'content_type'"
          :id="`import-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :required="field.required"
          :disabled="submitting"
        >
          <option v-if="!field.required" value="">— default —</option>
          <option
            v-for="option in contentTypeOptions"
            :key="option.value"
            :value="option.value"
          >{{ option.label }}</option>
        </select>
        <select
          v-else-if="field.field_type === 'bool'"
          :id="`import-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :required="field.required"
          :disabled="submitting"
        >
          <option value="">— default —</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        <input
          v-else
          :id="`import-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :type="
            field.field_type === 'int' || field.field_type === 'float'
              ? 'number'
              : 'text'
          "
          :step="field.field_type === 'float' ? 'any' : '1'"
          :required="field.required"
          :disabled="submitting"
          :placeholder="
            field.field_type === 'list' ? 'comma,separated,values' : ''
          "
        />
        <p v-if="field.description" class="help-text">{{ field.description }}</p>
      </div>

      <!--
        v-show (not v-if) keeps the live progress region in the DOM while the
        import runs so screen readers announce updates rather than treating
        each poll as a fresh insertion (WCAG 4.1.3).
      -->
      <div
        v-show="submitting"
        class="import-modal-progress-region"
        aria-live="polite"
      >
        <div v-if="importJob" class="import-modal-progress">
          <span
            v-if="importJob.progress_percent != null && importJob.total_items"
            class="import-modal-progress-bar"
            role="progressbar"
            :aria-valuenow="importJob.progress_percent"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="`Import progress: ${importJob.progress_percent}%`"
          >
            <span
              class="import-modal-progress-fill"
              :style="{ width: `${Math.min(100, importJob.progress_percent)}%` }"
            />
          </span>
          <span class="import-modal-progress-counts">{{ progressLabel }}</span>
          <span
            v-if="importJob.current_item"
            class="import-modal-progress-item"
          >{{ truncate(importJob.current_item, 50) }}</span>
        </div>
      </div>

      <!--
        Two banners with FIXED roles, both kept mounted via v-show. Assistive
        tech may not re-read a node whose role flips, so errors and progress/
        success live in separate elements (WCAG 4.1.3). Each signals state with
        text + an icon, never colour alone.
      -->
      <div
        v-show="resultError"
        class="sync-status-message sync-status-error"
        role="alert"
        aria-live="assertive"
      >
        <span aria-hidden="true">⚠ </span>{{ resultError }}
      </div>
      <div
        v-show="(result && !resultError) || (submitting && !result)"
        class="sync-status-message"
        role="status"
        aria-live="polite"
        :class="{
          'sync-status-success': result && !resultError,
          'sync-status-info': submitting && !result && !resultError,
        }"
      >
        <template v-if="result && !resultError">
          <span aria-hidden="true">✓ </span>{{ result.message }} {{ successCounts }}
        </template>
        <template v-else-if="submitting">Importing…</template>
      </div>

      <details
        v-if="result && skippedCount > 0"
        class="score-details import-modal-errors"
      >
        <summary>{{ skippedSummary }}</summary>
        <ul>
          <li v-for="(error, index) in result.errors" :key="index">{{ error }}</li>
        </ul>
      </details>

      <p
        v-show="!result && !submitting && disabledReason"
        id="import-disabled-reason"
        class="help-text"
      >{{ disabledReason }}</p>

      <div class="import-modal-actions">
        <template v-if="result">
          <button
            ref="doneButton"
            type="button"
            class="btn btn-primary"
            data-testid="import-done"
            @click="emit('close')"
          >Done</button>
        </template>
        <template v-else>
          <button
            type="button"
            class="btn btn-secondary"
            :disabled="submitting"
            @click="emit('close')"
          >Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            data-testid="import-submit"
            :disabled="!canSubmit"
            :aria-describedby="disabledReason ? 'import-disabled-reason' : undefined"
            @click="submit"
          >{{ submitting ? 'Importing…' : 'Import' }}</button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.import-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: var(--space-3);
}

.import-modal-content {
  background: var(--bg-card, var(--surface));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  width: 100%;
  max-width: 38rem;
  max-height: 90vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.import-modal-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.import-modal-field label {
  font-weight: 600;
  font-size: var(--text-sm);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.import-modal-field input[type="text"],
.import-modal-field input[type="number"],
.import-modal-field input[type="file"],
.import-modal-field select {
  padding: var(--space-2) var(--space-3);
  background: var(--bg-card, var(--surface));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
}

.import-modal-field input:focus-visible,
.import-modal-field select:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
}

.import-modal-progress {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.import-modal-progress-bar {
  position: relative;
  display: inline-block;
  width: 80px;
  height: 6px;
  background: var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
  vertical-align: middle;
}

.import-modal-progress-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.2s ease;
}

@media (prefers-reduced-motion: reduce) {
  .import-modal-progress-fill {
    transition: none;
  }
}

.import-modal-progress-counts {
  font-variant-numeric: tabular-nums;
}

.import-modal-progress-item {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: italic;
}

.import-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-default);
}
</style>
