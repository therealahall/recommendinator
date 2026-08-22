<script setup lang="ts">
import { computed } from 'vue'
import { domId } from '@/utils/format'
import type { SyncJobResponse } from '@/types/api'

const props = defineProps<{
  sourceId: string
  sourceName: string
  syncing: boolean
  job?: SyncJobResponse | null
}>()

// What the last run did to THIS source, which is the whole question a re-sync
// is run to answer: a count of items touched reads the same either way.
const resultLabel = computed<string>(() => {
  const job = props.job
  if (props.syncing || !job || job.status === 'running') return ''
  const entry = job.sources.find((slot) => slot.source === props.sourceName)
  if (!entry) return ''
  return (
    `${entry.items_added} added, ${entry.items_updated} updated, ` +
    `${entry.items_unchanged} unchanged`
  )
})

// An "All Sources" job carries every source's failures, and the remedy for one
// source is wrong on the next row.
const messages = computed<string[]>(() => {
  if (props.syncing || !props.job) return []
  return props.job.errors
    .filter((entry) => entry.source === props.sourceName)
    .map((entry) => entry.message)
})

// One entry per failed item, so a large library failing item by item would
// otherwise push the rest of the page off screen.
const MAX_SHOWN = 5
const shown = computed<string[]>(() => messages.value.slice(0, MAX_SHOWN))
const unshown = computed<number>(() => Math.max(0, messages.value.length - MAX_SHOWN))
const title = computed<string>(() => `Last sync errors for ${props.sourceName}`)
const titleId = computed<string>(() => domId('sync-errors-title', props.sourceId))
</script>

<template>
  <!--
    Plain content, not a live region: it renders on the poll that ends the
    sync, and a region arriving already populated is read as page content
    rather than a status change. The page-level sync banner announces (4.1.3).
    Outside the accordion header, whose slot is the trigger button's content:
    this would otherwise run into the source name in its accessible name.
  -->
  <p v-if="resultLabel" class="source-outcome-result" data-testid="source-sync-result">
    {{ resultLabel }}
  </p>
  <div v-if="messages.length" class="source-outcome-errors">
    <p :id="titleId" class="source-outcome-title" data-testid="source-sync-errors-title">
      {{ title }}
    </p>
    <ul
      class="source-outcome-list"
      data-testid="source-sync-errors"
      :aria-labelledby="titleId"
    >
      <li v-for="(message, index) in shown" :key="index">{{ message }}</li>
      <li v-if="unshown">… and {{ unshown }} more</li>
    </ul>
  </div>
</template>

<style scoped>
.source-outcome-result {
  margin: 0;
  padding: 0 var(--space-4) var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.source-outcome-errors {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 18%, transparent);
}

.source-outcome-title {
  margin: 0 0 var(--space-1);
  font-weight: 600;
}

.source-outcome-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.source-outcome-list li + li {
  margin-top: var(--space-1);
}
</style>
