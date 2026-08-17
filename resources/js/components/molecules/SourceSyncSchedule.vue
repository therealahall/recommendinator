<script setup lang="ts">
import { computed, ref } from 'vue'
import { domId, formatRelativeTime } from '@/utils/format'
import type { SyncRunResponse } from '@/types/api'

const props = defineProps<{
  sourceId: string
  sourceName: string
  /** The cadence in the words the API gave it. */
  intervalLabel: string
  lastRunAt: string | null
  lastRunStatus: string | null
  nextRunAt: string | null
  runs: SyncRunResponse[]
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  open: []
}>()

/** The two statuses ``sync_runs`` records, in words rather than a colour. */
const RUN_OUTCOMES: Record<string, string> = {
  completed: 'succeeded',
  failed: 'failed',
}

const open = ref(false)
const panelId = computed(() => domId('sync-runs', props.sourceId))

function toggle(): void {
  open.value = !open.value
  if (open.value) emit('open')
}

const lastRunRelative = computed(() =>
  props.lastRunAt === null ? '' : formatRelativeTime(props.lastRunAt),
)

const lastRunOutcome = computed(() =>
  props.lastRunStatus === null
    ? 'outcome unknown'
    : RUN_OUTCOMES[props.lastRunStatus] ?? props.lastRunStatus,
)

const nextRunLabel = computed(() =>
  props.nextRunAt === null ? '' : `Next run ${formatRelativeTime(props.nextRunAt)}`,
)

const lastRunFailed = computed(() => props.lastRunStatus === 'failed')

const rows = computed(() =>
  props.runs.map((run) => ({
    key: `${run.started_at}-${run.status}`,
    startedAt: run.started_at,
    when: formatRelativeTime(run.started_at),
    outcome: RUN_OUTCOMES[run.status] ?? run.status,
    failed: run.status === 'failed',
    counts:
      `${run.items_added} added, ${run.items_updated} updated, ` +
      `${run.items_unchanged} unchanged of ${run.total_items}`,
    errors: run.errors,
  })),
)

// Opens with the visible words in it, so speech input can say them back
// (WCAG 2.5.3); aria-expanded is what carries the state.
const toggleLabel = computed(() => `Recent runs for ${props.sourceName}`)

const statusText = computed(() => {
  if (!open.value) return ''
  if (props.loading) return 'Loading recent runs…'
  if (props.error) return `Error: ${props.error}`
  if (props.runs.length === 0) return 'No runs recorded yet.'
  return ''
})
</script>

<template>
  <div class="sync-schedule">
    <p class="sync-schedule-line" :data-testid="`sync-schedule-${sourceId}`">
      <span class="sync-schedule-cadence">Cadence: {{ intervalLabel }}</span>
      <span aria-hidden="true">·</span>
      <span :class="{ 'sync-schedule-failed': lastRunFailed }">
        <template v-if="lastRunAt">Last synced
          <time :datetime="lastRunAt">{{ lastRunRelative }}</time>, {{ lastRunOutcome }}
        </template>
        <template v-else>Never synced</template>
      </span>
      <template v-if="nextRunLabel">
        <span aria-hidden="true">·</span>
        <span>{{ nextRunLabel }}</span>
      </template>
    </p>

    <button
      type="button"
      class="sync-schedule-toggle"
      :data-testid="`run-history-toggle-${sourceId}`"
      :aria-expanded="open"
      :aria-controls="panelId"
      :aria-label="toggleLabel"
      @click="toggle"
    >Recent runs</button>

    <!-- Mounted before it has anything to say, and outside the panel: a region
         revealed already populated is read as page content (WCAG 4.1.3). -->
    <p
      class="sync-schedule-status"
      :data-testid="`run-history-status-${sourceId}`"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ statusText }}</p>

    <div :id="panelId" :hidden="!open">
      <ul
        v-if="rows.length"
        class="run-list"
        role="list"
        :data-testid="`run-history-${sourceId}`"
        :aria-label="`Recent runs for ${sourceName}`"
      >
        <li v-for="row in rows" :key="row.key" class="run-row">
          <p class="run-row-head">
            <time :datetime="row.startedAt">{{ row.when }}</time>
            <span :class="{ 'sync-schedule-failed': row.failed }">{{ row.outcome }}</span>
          </p>
          <p class="run-row-counts">{{ row.counts }}</p>
          <ul v-if="row.errors.length" class="run-row-errors" role="list">
            <li v-for="(message, index) in row.errors" :key="index">{{ message }}</li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.sync-schedule {
  padding: 0 var(--space-4) var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.sync-schedule-line {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: baseline;
}

.sync-schedule-cadence {
  font-weight: 600;
  color: var(--text-primary);
}

/* Reinforces the word "failed" beside it rather than replacing it (1.4.1). */
.sync-schedule-failed {
  color: var(--color-error-text);
}

/* Not .btn-ghost: its --text-muted misses 4.5:1 at this size (WCAG 1.4.3). */
.sync-schedule-toggle {
  padding: var(--space-1) 0;
  background: none;
  border: 0;
  color: var(--text-primary);
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.sync-schedule-toggle:hover {
  text-decoration-thickness: 2px;
}

.sync-schedule-status {
  margin: 0;
}

.run-list,
.run-row-errors {
  margin: 0;
  padding: 0;
  list-style: none;
}

.run-row {
  padding: var(--space-2) 0;
  border-top: 1px solid var(--border-subtle);
}

.run-row-head {
  margin: 0;
  display: flex;
  gap: var(--space-2);
  color: var(--text-primary);
}

.run-row-counts {
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.run-row-errors {
  margin-top: var(--space-1);
  color: var(--color-error-text);
}

.run-row-errors li {
  overflow-wrap: anywhere;
}
</style>
