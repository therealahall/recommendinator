<script setup lang="ts">
import { computed } from 'vue'
import type { ImportResponse } from '@/types/api'

const props = defineProps<{
  result: ImportResponse
}>()

// Precomputed rather than six hand-written pairs: the five outcomes plus the
// total are exactly what the CLI prints, and one list keeps them in that order.
const counts = computed(() => [
  { key: 'added', label: 'Added', value: props.result.added },
  { key: 'updated', label: 'Updated', value: props.result.updated },
  { key: 'unchanged', label: 'Unchanged', value: props.result.unchanged },
  { key: 'skipped', label: 'Skipped', value: props.result.skipped },
  { key: 'failed', label: 'Failed', value: props.result.failed },
  { key: 'total_rows', label: 'Rows read', value: props.result.total_rows },
])

// From the counts, not from the list: every miss records one line, but the list
// caps at 200 plus a tally, so counting it under-reports the whole-file refusal
// the cap exists for.
const missed = computed(() => props.result.skipped + props.result.failed)
</script>

<template>
  <div class="import-result" data-testid="import-result">
    <p v-if="result.filename" class="import-result-file">
      {{ result.filename }}
    </p>

    <dl class="import-counts">
      <div v-for="count in counts" :key="count.key" class="import-count">
        <dt>{{ count.label }}</dt>
        <dd :data-testid="`import-count-${count.key}`">{{ count.value }}</dd>
      </div>
    </dl>

    <!-- Its own paragraph, outside the misses: it happened to the file, so a
         heading counting refused rows would put it under a count of zero. -->
    <p
      v-for="note in result.notes"
      :key="note"
      class="import-callout import-note"
      data-testid="import-note"
    >{{ note }}</p>

    <!-- Not an error state and not styled as one. A file where most rows
         imported and two did not is a success that lists two rows. -->
    <div v-if="result.errors.length" class="import-callout">
      <!-- "Rows", because each message names its own unit: a JSON array
           entry is not a file line, and the heading cannot know which. -->
      <h4 class="import-misses-title">
        Rows that did not import ({{ missed }})
      </h4>
      <ul class="import-misses-list" data-testid="import-errors" role="list">
        <li v-for="message in result.errors" :key="message">{{ message }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.import-result {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.import-result-file {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.import-counts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-5);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.import-count {
  display: flex;
  flex-direction: column-reverse;
  min-width: 4.5rem;
}

.import-count dt {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-widest);
  color: var(--text-secondary);
}

.import-count dd {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}

/* One rule rather than a comma-grouped selector: contrast.test.ts finds a rule
   body by anchoring the selector at the start of a line, so a second selector
   in the group would be invisible to it. */
.import-callout {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--color-warning);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  background: var(--bg-elevated);
}

.import-note {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.import-misses-title {
  margin: 0 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.import-misses-list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.import-misses-list li + li {
  margin-top: var(--space-1);
}
</style>
