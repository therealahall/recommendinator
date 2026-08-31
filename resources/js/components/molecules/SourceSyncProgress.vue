<script setup lang="ts">
import { computed } from 'vue'
import { progressMilestone } from '@/utils/format'
import type { SyncJobResponse, SyncSourceProgressResponse } from '@/types/api'

const props = defineProps<{
  sourceName: string
  job?: SyncJobResponse | null
}>()

// A single-source run carries this source's counters at the top of the job;
// the umbrella "All Sources" run carries them in this source's own slot.
const progress = computed<Omit<SyncSourceProgressResponse, 'omitted_errors'> | null>(() => {
  const job = props.job
  if (!job || job.status !== 'running') return null
  if (job.source === props.sourceName) {
    return {
      source: job.source,
      items_processed: job.items_processed,
      total_items: job.total_items,
      current_item: job.current_item,
      progress_percent: job.progress_percent,
      items_added: job.items_added,
      items_updated: job.items_updated,
      items_unchanged: job.items_unchanged,
    }
  }
  return job.sources.find((entry) => entry.source === props.sourceName) || null
})

const label = computed<string>(() => {
  const entry = progress.value
  if (!entry) return ''
  if (entry.total_items != null && entry.total_items > 0) {
    const pct = entry.progress_percent != null ? ` (${entry.progress_percent}%)` : ''
    return `${entry.items_processed}/${entry.total_items}${pct}`
  }
  return `${entry.items_processed} items`
})

// Every configured source keeps one of these mounted, so a per-poll region is
// one sentence per source per tick. The end of the run is the page's own sync
// banner to announce; this one falls silent, which says nothing.
const announcement = computed<string>(() => {
  const entry = progress.value
  if (!entry) return ''
  const reached =
    entry.progress_percent == null ? 0 : progressMilestone(entry.progress_percent)
  return reached === 0
    ? `${props.sourceName} sync running.`
    : `${props.sourceName} sync ${reached}% complete.`
})
</script>

<template>
  <!-- Belongs outside the accordion trigger: inside it the counts joined the
       button's accessible name and role="progressbar" nested a widget role in
       a button. Mounted while silent, or the first poll announces nothing. -->
  <span class="source-progress" :class="{ 'source-progress--running': progress }">
    <span
      v-if="progress?.progress_percent != null"
      class="source-progress-bar"
      role="progressbar"
      :aria-valuenow="progress.progress_percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="`${sourceName} sync progress`"
    >
      <span
        class="source-progress-fill"
        :style="{ width: `${Math.min(100, progress.progress_percent)}%` }"
      />
    </span>
    <span
      class="source-progress-counts"
    ><span v-if="label" class="sr-only">{{ sourceName }}: </span>{{ label }}</span>
    <span
      v-if="progress?.current_item"
      class="source-progress-item"
    >{{ progress.current_item }}</span>
    <span
      class="sr-only"
      data-testid="sync-progress-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ announcement }}</span>
  </span>
</template>

<style scoped>
.source-progress {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.source-progress--running {
  padding: 0 var(--space-4) var(--space-2);
}

/* The page colour and an edge, for the reason input[type="range"] carries both:
   no token clears 3:1 against the accent and the card at once (WCAG 1.4.11). */
.source-progress-bar {
  position: relative;
  display: inline-block;
  width: 80px;
  height: 6px;
  background: var(--bg-primary);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
  vertical-align: middle;
}

.source-progress-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.2s ease;
}

@media (prefers-reduced-motion: reduce) {
  .source-progress-fill {
    transition: none;
  }
}

.source-progress-counts {
  font-variant-numeric: tabular-nums;
}

.source-progress-item {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: italic;
}
</style>
