<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import Accordion from '@/components/atoms/Accordion.vue'
import FileDropZone from '@/components/atoms/FileDropZone.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import ImportResultSummary from '@/components/molecules/ImportResultSummary.vue'
import type { ImportResponse } from '@/types/api'

const data = useDataStore()

const expanded = ref(false)
const formatsLoaded = ref(false)
const formatsLoading = ref(false)
const file = ref<File | null>(null)
const importerName = ref('')
const contentType = ref('book')
const importing = ref(false)
const result = ref<ImportResponse | null>(null)
const errorMessage = ref('')
// Its own ref, not the shared error slot: that slot is about the file and is
// cleared by the next pick, which would take this failure with it and leave
// Import refusing for a reason nothing on screen still gives.
const formatsError = ref('')
const status = ref('')

// Fetched on first expand: a closed panel announcing "Failed to load import
// formats" reports on a surface the operator has not asked to see.
async function ensureFormats(): Promise<void> {
  if (formatsLoaded.value || formatsLoading.value) return
  formatsLoading.value = true
  try {
    await data.loadImporters()
    if (!importerName.value && data.importers.length > 0) {
      importerName.value = data.importers[0].name
    }
    formatsLoaded.value = true
    if (status.value === formatsError.value) status.value = ''
    formatsError.value = ''
  } catch (err) {
    // Fixed lead sentence, not the error's: an API answering "Service
    // Unavailable" names nothing the operator can act on.
    const reason = err instanceof Error ? ` ${err.message}` : ''
    formatsError.value = `Failed to load import formats.${reason}`
    status.value = formatsError.value
  } finally {
    formatsLoading.value = false
  }
  // Swallowed on purpose: the templates are a convenience beside the picker,
  // and failing here would take down a panel that still imports the file the
  // operator already has.
  data.loadImportTemplates().catch(() => {})
}

async function onToggleExpanded(value: boolean): Promise<void> {
  expanded.value = value
  if (value) await ensureFormats()
}

// Named on the select as well as rendered beside it: the failure is the reason
// the list is empty, and a screen-reader user landing on the select is
// otherwise given an empty control and no explanation.
const formatDescribedBy = computed(() =>
  formatsError.value
    ? 'import-format-error import-format-description'
    : 'import-format-description',
)

const selectedImporter = computed(
  () => data.importers.find((entry) => entry.name === importerName.value) ?? null,
)

const needsContentType = computed(
  () => selectedImporter.value?.requires_content_type ?? false,
)

const effectiveContentType = computed(() =>
  needsContentType.value ? contentType.value : undefined,
)

const matchedTemplate = computed(
  () =>
    data.importTemplates.find(
      (entry) =>
        entry.importer === importerName.value &&
        entry.content_type === effectiveContentType.value,
    ) ?? null,
)

const templateUrl = computed(() => {
  const found = matchedTemplate.value
  if (!found) return ''
  const params = new URLSearchParams({
    importer: found.importer,
    content_type: found.content_type,
  })
  return `/api/import/templates/download?${params.toString()}`
})

const canImport = computed(() => file.value !== null && importerName.value !== '')

function onFileChosen(chosen: File | null, dropped: boolean): void {
  file.value = chosen
  result.value = null
  errorMessage.value = ''
  // Announced for a drop only: the picker path is already spoken by the input's
  // own value, and both would say the filename twice (WCAG 4.1.3). Cleared
  // otherwise, so a failed import cannot outlive the error text beside it.
  status.value = dropped && chosen ? `Selected file: ${chosen.name}` : ''
}

function summarise(imported: ImportResponse): string {
  // From the counts, not from the list: the list caps at 200 plus a tally, and
  // "200 rows did not import" for a refused 10,000-row export is worse than
  // silence.
  const missed = imported.skipped + imported.failed
  const misses =
    missed === 0 ? '' : ` ${missed} ${missed === 1 ? 'row' : 'rows'} did not import.`
  // Spoken, not just rendered: no count covers a note, so a screen-reader user
  // is otherwise never told the block beside the counts appeared at all.
  const notes = imported.notes.map((note) => ` ${note}.`).join('')
  return (
    `Imported ${imported.filename ?? 'the file'}: added ${imported.added}, ` +
    `updated ${imported.updated}, unchanged ${imported.unchanged}, ` +
    `skipped ${imported.skipped}, failed ${imported.failed}. ` +
    `${imported.total_rows} rows read.${misses}${notes}`
  )
}

async function submit(): Promise<void> {
  const chosen = file.value
  // aria-disabled does not block activation the way native disabled does, so
  // the in-flight guard lives here rather than on the button.
  if (importing.value || chosen === null || importerName.value === '') return

  importing.value = true
  result.value = null
  errorMessage.value = ''
  // The in-flight line is what makes a repeat import audible: re-importing the
  // same file to the same counts leaves the summary unchanged, and a region
  // whose text does not change announces nothing.
  status.value = `Importing ${chosen.name}…`
  try {
    const imported = await data.importFile(
      chosen,
      importerName.value,
      effectiveContentType.value,
    )
    result.value = imported
    status.value = summarise(imported)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Import failed'
    status.value = `Import failed. ${errorMessage.value}`
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div class="import-panel">
    <Accordion id="import" :expanded="expanded" @update:expanded="onToggleExpanded">
      <template #header>
        <span class="import-panel-name">Import a file</span>
      </template>

      <p class="help-text import-panel-lede">
        Read an export once and save what is in it. An import configures
        nothing and schedules nothing, so it adds no source to sync.
      </p>

      <div class="import-panel-body">
        <FileDropZone
          input-id="import-file"
          label="File"
          :file="file"
          @update:file="onFileChosen"
        />

        <div class="import-controls">
          <div class="import-pickers">
            <div class="import-field">
              <label for="import-format">Format</label>
              <select
                id="import-format"
                v-model="importerName"
                class="field"
                :aria-describedby="formatDescribedBy"
              >
                <option
                  v-for="entry in data.importers"
                  :key="entry.name"
                  :value="entry.name"
                >{{ entry.display_name }}</option>
              </select>
              <p
                v-if="formatsError"
                id="import-format-error"
                class="state state--error"
                data-testid="import-format-error"
              >{{ formatsError }}</p>
            </div>

            <div v-if="needsContentType" class="import-field">
              <label for="import-content-type">Content type</label>
              <TypeSelect
                id="import-content-type"
                v-model="contentType"
                class="field"
                :include-all="false"
              />
            </div>
          </div>

          <p id="import-format-description" class="help-text">
            {{ selectedImporter?.description }}
          </p>

          <!-- Beside the picker, not in the result: a Docker operator has no
               shell on the container, so this link is the only way to reach the
               file they are meant to fill in. -->
          <p v-if="templateUrl" class="import-template">
            <a
              class="btn btn-secondary btn-small"
              :href="templateUrl"
              :download="matchedTemplate?.filename"
              data-testid="import-template-link"
            >Download the {{ matchedTemplate?.filename }} template</a>
          </p>

          <div class="import-actions">
            <button
              type="button"
              class="btn btn-primary"
              data-testid="import-submit"
              :disabled="!canImport"
              :aria-disabled="importing || undefined"
              :aria-label="
                canImport
                  ? undefined
                  : file === null
                  ? 'Import — choose a file first'
                  : 'Import — import formats could not be loaded'
              "
              @click="submit"
            >{{ importing ? 'Importing…' : 'Import' }}</button>
          </div>
        </div>
      </div>

      <p v-if="errorMessage" class="state state--error import-error" data-testid="import-error">
        {{ errorMessage }}
      </p>

      <ImportResultSummary v-if="result" :result="result" />
    </Accordion>

    <!-- Outside the accordion, not inside it: collapsing sets `hidden` on the
         panel, which takes everything under it out of the accessibility tree —
         and a live region that leaves the tree stops announcing (WCAG 4.1.3). -->
    <p
      class="sr-only"
      data-testid="import-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ status }}</p>
  </div>
</template>

<style scoped>
.import-panel {
  margin-bottom: var(--space-5);
}

.import-panel-name {
  font-weight: 600;
}

.import-panel-lede {
  margin: 0 0 var(--space-4);
}

.import-panel-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-5);
  align-items: start;
}

@media (max-width: 800px) {
  .import-panel-body {
    grid-template-columns: minmax(0, 1fr);
  }
}

.import-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.import-pickers {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.import-field {
  display: flex;
  flex: 1 1 10rem;
  flex-direction: column;
  gap: var(--space-1);
}

.import-field label {
  font-size: var(--text-sm);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-primary);
}

.import-controls .help-text {
  margin: 0;
}

.import-template {
  margin: 0;
}

.import-actions {
  display: flex;
  justify-content: flex-start;
  margin-top: auto;
  padding-top: var(--space-1);
}

.import-error {
  margin-top: var(--space-4);
}

.import-panel :deep(.import-result) {
  margin-top: var(--space-4);
}
</style>
