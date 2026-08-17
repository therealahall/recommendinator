<script setup lang="ts">
import { computed } from 'vue'
import { formatRelativeTime } from '@/utils/format'

const props = defineProps<{
  sourceId: string
  /** The cadence in the words the API gave it. */
  intervalLabel: string
  lastRunAt: string | null
  lastRunStatus: string | null
  nextRunAt: string | null
}>()

/** The two statuses ``sync_runs`` records, in words rather than a colour. */
const RUN_OUTCOMES: Record<string, string> = {
  completed: 'succeeded',
  failed: 'failed',
}

const lastRunRelative = computed(() =>
  props.lastRunAt === null ? '' : formatRelativeTime(props.lastRunAt),
)

const lastRunOutcome = computed(() =>
  props.lastRunStatus === null
    ? 'outcome unknown'
    : RUN_OUTCOMES[props.lastRunStatus] ?? props.lastRunStatus,
)

const nextRunLabel = computed(() => {
  if (props.nextRunAt === null) return ''
  // A never-run source is dated now, so relative time would read as the past.
  if (Date.parse(props.nextRunAt) <= Date.now()) return 'Next run due now'
  return `Next run ${formatRelativeTime(props.nextRunAt)}`
})

const lastRunFailed = computed(() => props.lastRunStatus === 'failed')
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
</style>
