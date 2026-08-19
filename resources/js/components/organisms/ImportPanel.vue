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
const status = ref('')

// Fetched on first expand, the way a source accordion fetches its schema: a
// closed panel that announced "Import formats could not be loaded" would be
// reporting on a surface the operator has not asked to see.
async function ensureFormats(): Promise<void> {
  if (formatsLoaded.value || formatsLoading.value) return
  formatsLoading.value = true
  try {
    await data.loadImporters()
    if (!importerName.value && data.importers.length > 0) {
      importerName.value = data.importers[0].name
    }
    formatsLoaded.value = true
  } catch (err) {
    errorMessage.value =
      err instanceof Error ? err.message : 'Failed to load import formats'
    status.value = `Import formats could not be loaded. ${errorMessage.value}`
  } finally {
    formatsLoading.value = false
  }
  // Swallowed on purpose: the templates are a convenience beside the picker,
  // and an install that ships none still imports a file the operator already
  // has. Failing here would take the whole panel down with it.
  data.loadImportTemplates().catch(() => {})
}

async function onToggleExpanded(value: boolean): Promise<void> {
  expanded.value = value
  if (value) await ensureFormats()
}

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
                aria-describedby="import-format-description"
              >
                <option
                  v-for="entry in data.importers"
                  :key="entry.name"
                  :value="entry.name"
                >{{ entry.display_name }}</option>
              </select>
            </div>

            <div v-if="needsContentType" class="import-field">
              <label for="import-content-type">Content type</label>
              <TypeSelect
                id="import-content-type"
                v-model="contentType"
                :include-all="false"
              />
            </div>
          </div>

          <p id="import-format-description" class="help-text">
            {{ selectedImporter?.description }}
          </p>

          <!-- Beside the picker, not in the result: a Docker operator has no
               shell on the container, so this link is the only way to reach the
               file they are meant to fill in — and they need it before the
               upload, not after. -->
          <p v-if="templateUrl" class="import-template">
            <!-- Carries the button tokens rather than a link colour of its own:
                 --accent-light reaches only 3.3:1 on Snowstorm's white card. -->
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

      <p v-if="errorMessage" class="import-error" data-testid="import-error">
        {{ errorMessage }}
      </p>

      <ImportResultSummary v-if="result" :result="result" />
    </Accordion>

    <!-- Outside the accordion, not inside it: collapsing sets `hidden` on the
         panel, which takes everything under it out of the accessibility tree —
         and a live region that leaves the tree stops announcing (WCAG 4.1.3).
         Out here it is mounted and silent from first render either way. -->
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

.import-field :deep(select) {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-input);
  color: var(--text-primary);
  font: inherit;
  font-size: var(--text-sm);
}

.import-field :deep(select:focus-visible) {
  outline: 2px solid var(--border-focus);
  outline-offset: 1px;
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
  margin: var(--space-4) 0 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.import-panel :deep(.import-result) {
  margin-top: var(--space-4);
}
</style>
