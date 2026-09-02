<script setup lang="ts">
import { computed, ref } from 'vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import DuplicatePair from '@/components/molecules/DuplicatePair.vue'
import {
  SUGGESTION_LIMITS,
  decisionKey,
  refusalAlert,
  useDuplicatesStore,
} from '@/stores/duplicates'
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
    }
  }),
)

// Blank where the summary says a work was skipped, which this contradicts.
const emptyMessage = computed(() => {
  if (store.skippedNote) return ''
  return store.typeFilter
    ? 'No suspected duplicates of this type.'
    : 'No suspected duplicates. Nothing looks like the same work twice.'
})

function decide(
  index: number,
  run: () => Promise<void>,
  surviving: () => string = () => '',
): Promise<void> {
  return keepFocusInList(
    listEl,
    queueEl,
    index,
    () => rows.value.map((row) => row.key),
    run,
    () => refusalAlert(store.error),
    surviving,
  )
}

function blockHolding(copyId: number): string {
  const held = rows.value.find((row) =>
    row.suggestion.copies.some((copy) => copy.db_id === copyId),
  )
  return held?.key ?? ''
}
</script>

<template>
  <section
    ref="queueEl"
    class="card focus-fallback"
    aria-labelledby="dup-queue-heading"
    tabindex="-1"
  >
    <h3 id="dup-queue-heading" class="section-title dup-heading">Suspected duplicates</h3>
    <p class="help-text">
      Merging keeps one copy and folds the rest into it. Nothing is deleted, and
      every merge can be undone. Dismissing a copy keeps it off this work until
      you offer it again.
    </p>

    <div class="dup-filters">
      <TypeSelect
        class="field toolbar-select"
        :model-value="store.typeFilter"
        @update:model-value="store.setFilter('type', $event)"
      />
      <select
        class="field toolbar-select"
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

    <!-- Blanked only with nothing to keep: a reload unmounts the tabbed row. -->
    <div v-if="store.loading && rows.length === 0" class="state state--loading">
      <span class="spinner" /> Loading…
    </div>
    <div
      v-else-if="rows.length === 0 && emptyMessage"
      class="state state--empty"
      data-testid="dup-queue-empty"
    >
      <p class="state-title">{{ emptyMessage }}</p>
    </div>
    <ul v-else-if="rows.length" ref="listEl" class="dup-list" role="list">
      <DuplicatePair
        v-for="(row, index) in rows"
        :key="row.key"
        :suggestion="row.suggestion"
        :merging="row.merging"
        :declining="row.declining"
        @merge="
          (keep, drop) =>
            decide(index, () => store.merge(keep, drop), () => blockHolding(keep))
        "
        @decline="
          (copy, others) =>
            decide(index, () => store.declineCopy(copy, others), () =>
              decisionKey(others),
            )
        "
      />
    </ul>
  </section>
</template>

<style scoped>
.dup-heading {
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
