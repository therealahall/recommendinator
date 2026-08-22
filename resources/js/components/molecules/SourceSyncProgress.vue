<script setup lang="ts">
import { computed } from 'vue'
import type { SyncJobResponse, SyncSourceProgressResponse } from '@/types/api'

const props = defineProps<{
  sourceName: string
  job?: SyncJobResponse | null
}>()

// A single-source run carries this source's counters at the top of the job;
// the umbrella "All Sources" run carries them in this source's own slot.
const progress = computed<SyncSourceProgressResponse | null>(() => {
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
</script>

<template>
  <!--
    v-show (not v-if) keeps the live region in the DOM so JAWS/NVDA announce
    progress as values change rather than treating each poll as a fresh
    insertion (WCAG 4.1.3). Every deref below is null-safe so the children
    evaluate cleanly while the region is hidden.
  -->
  <span v-show="progress" class="source-progress" aria-live="polite">
    <span
      v-if="progress?.progress_percent != null"
      class="source-progress-bar"
      role="progressbar"
      :aria-valuenow="progress.progress_percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="`${sourceName} sync progress: ${progress.progress_percent}%`"
    >
      <span
        class="source-progress-fill"
        :style="{ width: `${Math.min(100, progress.progress_percent)}%` }"
      />
    </span>
    <span class="source-progress-counts">{{ label }}</span>
    <span
      v-if="progress?.current_item"
      class="source-progress-item"
    >{{ progress.current_item }}</span>
  </span>
</template>

<style scoped>
.source-progress {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.source-progress-bar {
  position: relative;
  display: inline-block;
  width: 80px;
  height: 6px;
  background: var(--border-default);
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
