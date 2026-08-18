<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import FileDropZone from '@/components/atoms/FileDropZone.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import ImportResultSummary from '@/components/molecules/ImportResultSummary.vue'
import type { ImportResponse } from '@/types/api'

const data = useDataStore()

const file = ref<File | null>(null)
const importerName = ref('')
const contentType = ref('book')
const importing = ref(false)
const result = ref<ImportResponse | null>(null)
const errorMessage = ref('')
const status = ref('')

onMounted(async () => {
  try {
    await data.loadImporters()
    if (!importerName.value && data.importers.length > 0) {
      importerName.value = data.importers[0].name
    }
  } catch (err) {
    errorMessage.value =
      err instanceof Error ? err.message : 'Failed to load import formats'
    status.value = `Import formats could not be loaded. ${errorMessage.value}`
  }
  // Swallowed on purpose: the templates are a convenience beside the picker,
  // and an install that ships none still imports a file the operator already
  // has. Failing here would take the whole panel down with it.
  data.loadImportTemplates().catch(() => {})
})

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

function onFileChosen(chosen: File | null): void {
  file.value = chosen
  result.value = null
  errorMessage.value = ''
}

function summarise(imported: ImportResponse): string {
  const missed = imported.errors.length
  const listed =
    missed === 0
      ? ''
      : ` ${missed} ${missed === 1 ? 'row is' : 'rows are'} listed below.`
  return (
    `Imported ${imported.filename ?? 'the file'}: added ${imported.added}, ` +
    `updated ${imported.updated}, unchanged ${imported.unchanged}, ` +
    `skipped ${imported.skipped}, failed ${imported.failed}. ` +
    `${imported.total_rows} rows read.${listed}`
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
  <section class="card import-panel" aria-labelledby="import-panel-heading">
    <div class="import-panel-intro">
      <h3 id="import-panel-heading">Import a file</h3>
      <p class="help-text">
        Read an export once and save what is in it. An import configures
        nothing and schedules nothing, so it adds no source to sync.
      </p>
    </div>

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
            @click="submit"
          >{{ importing ? 'Importing…' : 'Import' }}</button>
        </div>
      </div>
    </div>

    <p v-if="errorMessage" class="import-error" data-testid="import-error">
      {{ errorMessage }}
    </p>

    <ImportResultSummary v-if="result" :result="result" />

    <!-- Outside every branch above, so it is already in the accessibility tree
         when the first import finally gives it something to say (WCAG 4.1.3). -->
    <p
      class="sr-only"
      data-testid="import-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ status }}</p>
  </section>
</template>

<style scoped>
.import-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.import-panel-intro h3 {
  margin: 0 0 var(--space-1);
}

.import-panel-intro .help-text {
  margin: 0;
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
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
  color: var(--text-primary);
  font-size: var(--text-sm);
}
</style>
