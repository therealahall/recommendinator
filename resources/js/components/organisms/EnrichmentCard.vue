<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { formatContentType, truncate } from '@/utils/format'
import EnrichmentReset from '@/components/molecules/EnrichmentReset.vue'
import TypePills from '@/components/atoms/TypePills.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'

const data = useDataStore()
const enrichType = ref('')
const retryNotFound = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')

const stats = computed(() => data.enrichmentStats)
const running = computed(() => data.enrichmentJob?.running === true)
const typeLabel = computed(() =>
  enrichType.value ? formatContentType(enrichType.value) : '',
)
// The manager caps this list itself, last entry a count of the rest, so the
// whole of it renders: the line saying why a run stopped is usually the last.
const jobErrors = computed<string[]>(() => data.enrichmentJob?.errors ?? [])
// The count, not the lines: fifty of them read aloud is not a status message.
const errorsAnnouncement = computed(() => {
  const count = jobErrors.value.length
  if (count === 0) return ''
  return `This enrichment run reported ${count} ${count === 1 ? 'error' : 'errors'}.`
})
const resettable = computed(() =>
  enrichType.value || !stats.value
    ? null
    : { all: stats.value.resettable, ...stats.value.by_provider },
)

function control(testid: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)
}

// aria-disabled rather than disabled on the buttons below, because `disabled`
// blurs the control the user just pressed; the guard here is what stops the
// second activation.
async function run(action: () => Promise<string>): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    message.value = await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Unknown error'
  } finally {
    busy.value = false
  }
  // Whichever button the outcome unmounts, the keyboard lands on the refusal or
  // on the one control that is always there (WCAG 2.4.3).
  await nextTick()
  const landing = error.value ? 'enrichment-error' : 'enrichment-start'
  const focused = document.activeElement
  if (focused === null || focused === document.body || error.value) {
    control(landing)?.focus()
  }
}

const onEnable = () => run(() => data.enableEnrichment().then(() => 'Enrichment is on.'))
// aria-disabled does not block activation, so the running guard lives here.
const onEnrich = () =>
  running.value
    ? Promise.resolve()
    : run(() => data.startEnrichment(enrichType.value || undefined, retryNotFound.value))
const onStop = () => run(() => data.stopEnrichment())
const onReset = (provider: string) =>
  run(() => data.resetEnrichment(enrichType.value || undefined, provider))
</script>

<template>
  <div class="card">
    <h3>Metadata Enrichment</h3>
    <p class="help-text">
      Enrichment adds genres, tags, and descriptions from external APIs (TMDB,
      OpenLibrary, RAWG).
    </p>

    <template v-if="!data.enrichmentEnabled">
      <p class="enrichment-setup" data-testid="enrichment-setup">
        It is off, so recommendations run on whatever your sources happened to send.
      </p>
      <button
        type="button"
        class="btn btn-primary"
        data-testid="enrichment-enable"
        :aria-disabled="busy || undefined"
        @click="onEnable"
      >Turn on enrichment</button>
    </template>

    <template v-else>
      <p v-if="data.enrichmentStatsError" class="enrichment-stats-error">
        Could not read the enrichment counts: {{ data.enrichmentStatsError }}
      </p>
      <div v-else-if="stats">
        <div v-if="stats.total === 0" class="empty-state">
          No items to enrich. Sync some content first.
        </div>
        <div v-else class="enrichment-summary">
          <span>{{ stats.enriched }}/{{ stats.total }}
            ({{ Math.round((stats.enriched / stats.total) * 100) }}% enriched)</span>
        </div>
      </div>

      <div
        class="enrichment-status"
        aria-live="polite"
        aria-atomic="true"
      >
        <template v-if="data.enrichmentJob?.running">
          <span class="spinner" aria-hidden="true" />
          <span class="sr-only">Enriching: </span>
          {{ data.enrichmentJob.current_item
            ? truncate(data.enrichmentJob.current_item, 50)
            : 'Processing...' }}
          ({{ data.enrichmentJob.items_processed }}/{{ data.enrichmentJob.total_items }}
          - {{ Math.round(data.enrichmentJob.progress_percent) }}%)
        </template>
      </div>

      <div class="enrichment-toolbar">
        <TypePills v-model="enrichType" />

        <div class="toolbar-divider" />

        <ToggleSwitch v-model="retryNotFound" label="Retry Not Found" />

        <div class="toolbar-divider" />

        <div class="toolbar-right">
          <EnrichmentReset
            :type-label="typeLabel"
            :resettable="resettable"
            :busy="busy || running"
            @reset="onReset"
          />
          <button
            v-if="running"
            type="button"
            class="btn btn-secondary"
            data-testid="enrichment-stop"
            :aria-disabled="busy || undefined"
            @click="onStop"
          >Stop</button>
          <button
            type="button"
            class="btn btn-primary"
            data-testid="enrichment-start"
            :aria-disabled="busy || running || undefined"
            @click="onEnrich"
          >Enrich</button>
        </div>
      </div>

      <div v-if="jobErrors.length" class="enrichment-errors">
        <p id="enrichment-errors-title" class="enrichment-errors-title">
          Errors reported by this run
        </p>
        <ul
          class="enrichment-errors-list"
          data-testid="enrichment-errors"
          role="list"
          aria-labelledby="enrichment-errors-title"
        >
          <li v-for="(line, index) in jobErrors" :key="index">{{ line }}</li>
        </ul>
      </div>
    </template>

    <!-- Mounted while silent: inserted populated they read as content (4.1.3).
         The last is all a poll-delivered result has; nothing else announces it. -->
    <p
      class="enrichment-error focus-fallback"
      data-testid="enrichment-error"
      role="alert"
      tabindex="-1"
    >{{ error }}</p>
    <p
      class="enrichment-message"
      data-testid="enrichment-message"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ message }}</p>
    <p
      class="sr-only"
      data-testid="enrichment-errors-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ errorsAnnouncement }}</p>
  </div>
</template>

<style scoped>
.enrichment-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.enrichment-status {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.enrichment-status:not(:empty) {
  margin-top: var(--space-3);
}

.enrichment-errors {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.enrichment-errors-title {
  margin: 0 0 var(--space-1);
  font-weight: 600;
}

.enrichment-errors-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.enrichment-errors-list li + li {
  margin-top: var(--space-1);
}

.enrichment-summary {
  margin-bottom: var(--space-3);
}

.enrichment-setup {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.enrichment-stats-error,
.enrichment-error {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-error-text);
}

.enrichment-error:not(:empty) {
  margin-top: var(--space-3);
}

.enrichment-message {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.enrichment-message:not(:empty) {
  margin-top: var(--space-3);
}
</style>
