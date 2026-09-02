<script setup lang="ts">
import { computed, ref } from 'vue'
import { decisionKey, refusalAlert, useDuplicatesStore } from '@/stores/duplicates'
import { keepFocusInList } from '@/utils/focus'

const store = useDuplicatesStore()
const historyEl = ref<HTMLElement | null>(null)
const mergesEl = ref<HTMLElement | null>(null)
const declinedEl = ref<HTMLElement | null>(null)

const mergeRows = computed(() =>
  store.mergeRows.map(({ record, blocked }) => ({
    id: record.id,
    absorbedTitle: record.absorbed_title,
    survivorTitle: record.survivor_title,
    blocked,
    reasonId: `merge-reason-${record.id}`,
    meta: [
      `merge ${record.id}`,
      record.evidence_label,
      `${record.merged_at.slice(0, 10)} ${record.merged_at.slice(11, 16)} UTC`,
    ].join(' · '),
    undoing: store.isPending(`undo:${record.id}`),
  })),
)

const declinedRows = computed(() =>
  store.declined.map((pair) => {
    const key = decisionKey([pair.one_id, pair.other_id])
    return {
      key,
      oneId: pair.one_id,
      otherId: pair.other_id,
      text: `“${pair.one_title}” (row ${pair.one_id}) and “${pair.other_title}” (row ${pair.other_id})`,
      lifting: store.isPending(`undecline:${key}`),
    }
  }),
)

function undo(index: number, mergeId: number): Promise<void> {
  return keepFocusInList(
    mergesEl,
    historyEl,
    index,
    () => mergeRows.value.map((row) => String(row.id)),
    () => store.undoMerge(mergeId),
    () => refusalAlert(store.error),
  )
}

function offerAgain(index: number, oneId: number, otherId: number): Promise<void> {
  return keepFocusInList(
    declinedEl,
    historyEl,
    index,
    () => declinedRows.value.map((row) => row.key),
    () => store.offerAgain(oneId, otherId),
    () => refusalAlert(store.error),
  )
}
</script>

<template>
  <section
    ref="historyEl"
    class="card focus-fallback"
    aria-labelledby="dup-history-heading"
    tabindex="-1"
  >
    <h3 id="dup-history-heading" class="section-title">What you have decided</h3>

    <h4 id="dup-merges-heading" class="section-subtitle dup-subheading">Merges</h4>
    <p class="help-text">
      Newest first, which is the order they come back off in. The row that was
      folded in keeps everything it had.
    </p>
    <p v-if="mergeRows.length === 0" class="state">No merges yet.</p>
    <ul
      v-else
      ref="mergesEl"
      class="dup-log"
      role="list"
      aria-labelledby="dup-merges-heading"
    >
      <li v-for="(row, index) in mergeRows" :key="row.id" class="dup-log-row">
        <div class="dup-log-body">
          <p class="dup-log-text">
            “{{ row.absorbedTitle }}” folded into “{{ row.survivorTitle }}”
          </p>
          <p class="dup-log-meta">{{ row.meta }}</p>
          <p v-if="row.blocked" :id="row.reasonId" class="dup-log-reason">
            {{ row.blocked }}
          </p>
        </div>
        <button
          type="button"
          class="btn btn-secondary btn-small"
          :aria-disabled="row.blocked !== '' || row.undoing || undefined"
          :aria-describedby="row.blocked ? row.reasonId : undefined"
          @click="row.blocked || row.undoing ? undefined : undo(index, row.id)"
        >{{ row.undoing ? 'Undoing…' : 'Undo' }}</button>
      </li>
    </ul>

    <h4 id="dup-declined-heading" class="section-subtitle dup-subheading">Dismissed pairs</h4>
    <p v-if="declinedRows.length === 0" class="state">No pairs dismissed.</p>
    <ul
      v-else
      ref="declinedEl"
      class="dup-log"
      role="list"
      aria-labelledby="dup-declined-heading"
    >
      <li v-for="(row, index) in declinedRows" :key="row.key" class="dup-log-row">
        <div class="dup-log-body">
          <p class="dup-log-text">{{ row.text }}</p>
        </div>
        <button
          type="button"
          class="btn btn-secondary btn-small"
          :aria-disabled="row.lifting || undefined"
          @click="
            row.lifting ? undefined : offerAgain(index, row.oneId, row.otherId)
          "
        >{{ row.lifting ? 'Offering…' : 'Offer again' }}</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.dup-subheading {
  margin-top: var(--space-5);
  margin-bottom: var(--space-1);
}

.dup-log {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: 0;
  list-style: none;
}

.dup-log-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.dup-log-body {
  min-width: 0;
}

.dup-log-text {
  color: var(--text-primary);
}

.dup-log-meta {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.dup-log-reason {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-warning);
}
</style>
