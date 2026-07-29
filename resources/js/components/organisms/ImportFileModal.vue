<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useFocusTrap } from '@/composables/useFocusTrap'
import { ApiError, apiErrorDetail } from '@/composables/useApi'
import { useDataStore } from '@/stores/data'
import { humanizeKey, truncate } from '@/utils/format'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'
import { MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from '@/constants/upload'
import type { ImportResultResponse, ImportSourceResponse } from '@/types/api'

// Shown both by the client-side size check and by a server 413, so an
// oversized file reads the same however it is caught.
const OVERSIZE_MESSAGE =
  `That file is larger than the ${MAX_UPLOAD_MB} MB limit. Split the export ` +
  'into smaller files, or import it with the CLI, which has no size cap.'

// Every rejection POST /api/import can answer with, and the copy it earns. A
// status missing here falls through to GENERIC_MESSAGE, which tells the user
// to retry — right for a 500, wrong for anything they can act on. Adding a
// status is one line.
const STATUS_MESSAGES = new Map<number, string>([
  [
    400,
    "We couldn't read that file. Check that it matches the selected format and try again.",
  ],
  // The server guard is per plugin, so it only rejects a second import of the
  // same source; the copy must not promise more than that.
  [
    409,
    'An import from this source is already running. Wait for it to finish, then try again.',
  ],
  [413, OVERSIZE_MESSAGE],
  [422, "That import source isn't available. Pick another format."],
  // The upload middleware's concurrency cap. Retrying works, but only once
  // something else finishes, so say what the user is waiting on.
  [
    429,
    'Too many imports are already running. Wait for one to finish, then try again.',
  ],
  // Storage or config never came up. Retrying changes nothing until the server
  // is fixed, so point at the server rather than at the Import button.
  [
    503,
    "Imports are unavailable: the server's storage or configuration didn't " +
      'load. Check the server logs, then try again.',
  ],
])

const GENERIC_MESSAGE =
  'Something went wrong during the import. Please try again.'

// The import has not started yet, so GENERIC_MESSAGE would be a lie here.
const LOAD_FAILED_MESSAGE = "Couldn't load import sources."

const emit = defineEmits<{
  close: []
}>()

const data = useDataStore()
const modalContent = ref<HTMLElement | null>(null)
const doneButton = ref<HTMLButtonElement | null>(null)
const submitButton = ref<HTMLButtonElement | null>(null)
useFocusTrap(modalContent, () => emit('close'))

const sourceName = ref<string>('')
const file = ref<File | null>(null)
const fieldValues = ref<Record<string, string>>({})
const submitting = ref(false)
const result = ref<ImportResultResponse | null>(null)
const resultError = ref('')

// Through STATUS_MESSAGES like every other rejection, so a status code never
// reaches the banner. A 503 is the one the listing realistically produces, and
// its copy — the server's storage or config didn't load — is exactly why the
// list is empty, so it is worth saying after naming what failed.
function loadFailureMessage(error: unknown): string {
  const canned =
    error instanceof ApiError ? STATUS_MESSAGES.get(error.status) : undefined
  return `${LOAD_FAILED_MESSAGE} ${canned ?? 'Please try again.'}`
}

onMounted(async () => {
  try {
    await data.loadImportSources()
  } catch (err) {
    resultError.value = loadFailureMessage(err)
    return
  }
  if (data.importSources.length > 0 && !sourceName.value) {
    sourceName.value = data.importSources[0].name
  }
})

const selectedSource = computed<ImportSourceResponse | undefined>(() =>
  data.importSources.find((s) => s.name === sourceName.value),
)

// `descriptionId` is precomputed rather than derived in the template: deriving
// it there would re-run per field on every render of the v-for.
const visibleFields = computed(() =>
  (selectedSource.value?.fields ?? [])
    .filter((f) => !f.sensitive)
    .map((field) => ({
      ...field,
      descriptionId: field.description
        ? `import-field-${field.name}-desc`
        : undefined,
    })),
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

// A failed import announces in the assertive banner but renders no Done
// button, so nothing pulls focus back. If focus drifted outside the dialog
// during the upload, put it on Import so retrying is one keystroke away
// rather than a full page traversal (WCAG 2.4.3).
function recoverFocus(): void {
  const container = modalContent.value
  if (!container || container.contains(document.activeElement)) return
  ;(submitButton.value ?? container).focus()
}

const contentTypeOptions = computed(() => {
  const allowed = new Set(selectedSource.value?.content_types ?? [])
  return CONTENT_TYPE_OPTIONS.filter(
    (option) => option.value !== '' && allowed.has(option.value),
  )
})

// Each importer declares the extensions it reads, so the picker filter and
// the help text below follow the source rather than guessing from its name.
const acceptedExtensions = computed(
  () => selectedSource.value?.accepted_extensions ?? [],
)

const acceptAttr = computed(() => acceptedExtensions.value.join(','))

const acceptedExtensionsText = computed(() => {
  const extensions = acceptedExtensions.value
  if (extensions.length === 0) return ''
  const label = extensions.length === 1 ? 'file type' : 'file types'
  return `Accepted ${label}: ${extensions.join(', ')}`
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

const fileTooLarge = computed(
  () => !!file.value && file.value.size > MAX_UPLOAD_BYTES,
)

const canSubmit = computed(
  () =>
    !!selectedSource.value &&
    !!file.value &&
    !fileTooLarge.value &&
    requiredFilled.value &&
    !submitting.value &&
    !anyJobRunning.value,
)

const disabledReason = computed(() => {
  // No source means the listing failed or came back empty. `requiredFilled` is
  // vacuously true with no fields, so without this branch Import looks enabled
  // and then does nothing at all when clicked.
  if (!selectedSource.value) {
    return 'No import sources are available. Check the server logs, then reload.'
  }
  if (!file.value) return 'Choose a file to import.'
  if (!requiredFilled.value) return 'Fill in all required fields.'
  // syncStatus can still read 'running' for up to one 2s poll after a failure,
  // so suppress the hint while an error is showing rather than contradicting it.
  if (anyJobRunning.value && !resultError.value) {
    return 'Wait for the running job to finish, then try again.'
  }
  return ''
})

const disabledReasonShown = computed(
  () => !result.value && !submitting.value && !!disabledReason.value,
)

// Both the reason hint and the error banner explain why Import cannot proceed;
// the oversize case only ever produces the latter. Referencing whichever is
// visible keeps an explanation reachable from the button (WCAG 3.3.1).
const submitDescribedBy = computed(() => {
  const ids: string[] = []
  if (resultError.value) ids.push('import-error')
  if (disabledReasonShown.value) ids.push('import-disabled-reason')
  return ids.length > 0 ? ids.join(' ') : undefined
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

// aria-valuetext, not the accessible name: baking the percentage into the name
// makes AT read it twice and leaves it stale between renders.
const progressValueText = computed(() =>
  importJob.value?.progress_percent != null
    ? `${importJob.value.progress_percent}% complete`
    : '',
)

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
  // Report an oversized file now rather than making the user upload the whole
  // thing only to be told by the server's 413 that it could never succeed.
  resultError.value = fileTooLarge.value ? OVERSIZE_MESSAGE : ''
}

function messageForError(error: ApiError): string {
  const canned = STATUS_MESSAGES.get(error.status) ?? GENERIC_MESSAGE
  // 400 is the one status whose detail says something the client cannot work
  // out for itself: which import option the plugin refused, and why. The
  // canned copy blames the file, which is the wrong diagnosis when the file is
  // fine and an option value is not.
  return error.status === 400 ? (apiErrorDetail(error) ?? canned) : canned
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
      err instanceof ApiError ? messageForError(err) : GENERIC_MESSAGE
    void nextTick(recoverFocus)
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
        JSON, and Markdown, plus Goodreads and StoryGraph CSV exports.
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
        <!--
          Keyed on the source so Vue replaces the node when the user switches
          format. `file` alone is a component ref: clearing it leaves the native
          control still showing the old filename, contradicting the hint below
          it, and re-picking the same file fires no `change` event.
        -->
        <input
          id="import-file"
          :key="sourceName"
          type="file"
          :accept="acceptAttr || undefined"
          :disabled="submitting"
          :aria-describedby="
            acceptedExtensionsText ? 'import-file-accepted' : undefined
          "
          @change="onFileChange"
        />
        <p
          v-if="acceptedExtensionsText"
          id="import-file-accepted"
          class="help-text"
        >{{ acceptedExtensionsText }}</p>
        <p v-if="file" class="help-text">Selected file: {{ file.name }}</p>
      </div>

      <div
        v-for="field in visibleFields"
        :key="field.name"
        class="import-modal-field"
      >
        <!--
          The schema carries no display label, so humanize the key for the
          visible label (WCAG 2.4.6) while the raw key keeps driving the
          for/id pairing and the request payload.
        -->
        <label :for="`import-field-${field.name}`">
          {{ humanizeKey(field.name) }}
          <span v-if="field.required" aria-hidden="true">*</span>
        </label>
        <!--
          The description is wired to whichever control this branch renders, so
          it is read on focus rather than being visible-but-silent (WCAG 3.3.2).
        -->
        <select
          v-if="field.name === 'content_type'"
          :id="`import-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :required="field.required"
          :disabled="submitting"
          :aria-describedby="field.descriptionId"
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
          :aria-describedby="field.descriptionId"
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
          :aria-describedby="field.descriptionId"
          :placeholder="
            field.field_type === 'list' ? 'comma,separated,values' : ''
          "
        />
        <p
          v-if="field.descriptionId"
          :id="field.descriptionId"
          class="help-text"
        >{{ field.description }}</p>
      </div>

      <!--
        Deliberately NOT a live region: the sync poll rewrites these counts
        every 2s, and announcing each one floods the polite queue and buries
        the eventual result behind it. The progressbar carries the value for
        assistive tech to read on demand instead (WCAG 4.1.3); the coarse
        "Importing…" / result milestones ride the status banner below.
      -->
      <!--
        aria-busy scopes to the churning counts, NOT the dialog. On the dialog
        it covers every descendant, and AT may drop updates to a busy subtree —
        which would swallow the "Importing…" polite announcement below, since
        that lands in the same DOM flush that sets aria-busy (WCAG 4.1.3).
      -->
      <div
        v-show="submitting"
        class="import-modal-progress-region"
        :aria-busy="submitting"
      >
        <div v-if="importJob" class="import-modal-progress">
          <span
            v-if="importJob.progress_percent != null && importJob.total_items"
            class="import-modal-progress-bar"
            role="progressbar"
            :aria-valuenow="importJob.progress_percent"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-valuetext="progressValueText"
            aria-label="Import progress"
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
        id="import-error"
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
        data-testid="import-status"
        :class="{
          'sync-status-success': result && !resultError && !result.warning,
          'sync-status-warning': result && !resultError && !!result.warning,
          'sync-status-info': submitting && !result && !resultError,
        }"
      >
        <template v-if="result && !resultError">
          <span aria-hidden="true">{{ result.warning ? '⚠ ' : '✓ ' }}</span
          >{{ result.message }} {{ successCounts }}
          <span
            v-if="result.warning"
            class="import-modal-warning"
            data-testid="import-warning"
          >{{ result.warning }}</span>
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
        v-show="disabledReasonShown"
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
            @click="emit('close')"
          >Cancel</button>
          <!--
            aria-disabled rather than disabled: a disabled button is dropped
            from the tab order and blurred, which empties the focus trap for
            the whole upload and hides its description. submit() already
            refuses to run when canSubmit is false (WCAG 2.4.3).
          -->
          <button
            ref="submitButton"
            type="button"
            class="btn btn-primary"
            data-testid="import-submit"
            :aria-disabled="!canSubmit"
            :aria-describedby="submitDescribedBy"
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

.import-modal-warning {
  display: block;
  margin-top: var(--space-1);
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
