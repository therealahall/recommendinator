<script setup lang="ts">
import { computed, ref } from 'vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import DuplicatePair from '@/components/molecules/DuplicatePair.vue'
import { SUGGESTION_LIMITS, decisionKey, useDuplicatesStore } from '@/stores/duplicates'
import { keepFocusInList } from '@/utils/focus'

const store = useDuplicatesStore()
const queueEl = ref<HTMLElement | null>(null)
const listEl = ref<HTMLElement | null>(null)

const rows = computed(() =>
  store.suggestions.map((suggestion) => {
    const key = decisionKey(suggestion.copies.map((copy) => copy.db_id))
    return {
      key,
      suggestion,
      merging: store.isPending(`merge:${key}`),
      declining: store.isPending(`decline:${key}`),
      error: store.errorFor(`merge:${key}`) || store.errorFor(`decline:${key}`),
    }
  }),
)

const emptyMessage = computed(() =>
  store.typeFilter
    ? 'No suspected duplicates of this type.'
    : 'No suspected duplicates. Nothing looks like the same work twice.',
)

function decide(index: number, run: () => Promise<void>): Promise<void> {
  return keepFocusInList(
    listEl,
    queueEl,
    index,
    () => rows.value.map((row) => row.key),
    run,
  )
}
</script>

<template>
  <section
    ref="queueEl"
    class="card focus-fallback"
    aria-labelledby="dup-queue-heading"
    tabindex="-1"
  >
    <h3 id="dup-queue-heading" class="dup-heading">Suspected duplicates</h3>
    <p class="help-text">
      Merging keeps one copy and folds the rest into it. Nothing is deleted, and
      every merge can be undone. Dismissing a copy keeps it off this work until
      you offer it again.
    </p>

    <div class="dup-filters">
      <TypeSelect
        class="toolbar-select"
        :model-value="store.typeFilter"
        @update:model-value="store.setFilter('type', $event)"
      />
      <select
        class="toolbar-select"
        aria-label="Works to offer at once"
        :value="String(store.limit)"
        @change="store.setFilter('limit', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="size in SUGGESTION_LIMITS" :key="size" :value="String(size)">
          {{ size }} at a time
        </option>
      </select>
    </div>

    <p class="dup-summary">{{ store.summary }}</p>

    <!-- Blanked only with nothing to keep: the reload a decision runs would
         otherwise unmount the row the operator has tabbed on to. -->
    <div v-if="store.loading && rows.length === 0" class="empty-state">
      <span class="spinner" /> Loading…
    </div>
    <div v-else-if="rows.length === 0" class="empty-state">{{ emptyMessage }}</div>
    <ul v-else ref="listEl" class="dup-list" role="list">
      <DuplicatePair
        v-for="(row, index) in rows"
        :key="row.key"
        :suggestion="row.suggestion"
        :merging="row.merging"
        :declining="row.declining"
        :error="row.error"
        @merge="(keep, drop) => decide(index, () => store.merge(keep, drop))"
        @decline="(copy, others) => decide(index, () => store.declineCopy(copy, others))"
      />
    </ul>
  </section>
</template>

<style scoped>
.dup-heading {
  font-size: var(--text-lg);
  font-weight: 600;
  margin-bottom: var(--space-2);
}

.dup-filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.dup-summary {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.dup-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: 0;
  list-style: none;
}
</style>
