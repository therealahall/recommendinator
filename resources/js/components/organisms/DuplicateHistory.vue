<script setup lang="ts">
import { computed } from 'vue'
import { pairKey, useDuplicatesStore } from '@/stores/duplicates'

const store = useDuplicatesStore()

// Only the operator makes a merge, and storage folds anything an older build
// recorded back onto `manual`, so this is the whole set.
const MERGE_EVIDENCE: Record<string, string> = {
  manual: 'your choice',
}

const mergeRows = computed(() =>
  store.mergeRows.map(({ record, blocked }) => ({
    id: record.id,
    absorbedTitle: record.absorbed_title,
    survivorTitle: record.survivor_title,
    blocked,
    blockedId: `merge-blocked-${record.id}`,
    meta: [
      `merge ${record.id}`,
      MERGE_EVIDENCE[record.evidence] ?? record.evidence,
      `${record.merged_at.slice(0, 10)} ${record.merged_at.slice(11, 16)} UTC`,
    ].join(' · '),
    undoing: store.isPending(`undo:${record.id}`),
  })),
)

const declinedRows = computed(() =>
  store.declined.map((pair) => ({
    key: pairKey(pair.one_id, pair.other_id),
    oneId: pair.one_id,
    otherId: pair.other_id,
    text: `“${pair.one_title}” (row ${pair.one_id}) and “${pair.other_title}” (row ${pair.other_id})`,
    lifting: store.isPending(`undecline:${pairKey(pair.one_id, pair.other_id)}`),
  })),
)
</script>

<template>
  <section class="card" aria-labelledby="dup-history-heading">
    <h3 id="dup-history-heading" class="dup-heading">What you have decided</h3>

    <h4 id="dup-merges-heading" class="dup-subheading">Merges</h4>
    <p class="help-text">
      Newest first, which is the order they come back off in. The row that was
      folded in keeps everything it had.
    </p>
    <p v-if="mergeRows.length === 0" class="empty-state">No merges yet.</p>
    <ul v-else class="dup-log" role="list" aria-labelledby="dup-merges-heading">
      <li v-for="row in mergeRows" :key="row.id" class="dup-log-row">
        <div class="dup-log-body">
          <p class="dup-log-text">
            “{{ row.absorbedTitle }}” folded into “{{ row.survivorTitle }}”
          </p>
          <p class="dup-log-meta">{{ row.meta }}</p>
          <p v-if="row.blocked" :id="row.blockedId" class="dup-log-blocked">
            {{ row.blocked }}
          </p>
        </div>
        <button
          type="button"
          class="btn btn-secondary btn-small"
          :aria-disabled="row.blocked !== '' || row.undoing || undefined"
          :aria-describedby="row.blocked ? row.blockedId : undefined"
          @click="row.blocked || row.undoing ? undefined : store.undoMerge(row.id)"
        >{{ row.undoing ? 'Undoing…' : 'Undo' }}</button>
      </li>
    </ul>

    <h4 id="dup-declined-heading" class="dup-subheading">Dismissed pairs</h4>
    <p v-if="declinedRows.length === 0" class="empty-state">
      No pairs dismissed.
    </p>
    <ul v-else class="dup-log" role="list" aria-labelledby="dup-declined-heading">
      <li v-for="row in declinedRows" :key="row.key" class="dup-log-row">
        <div class="dup-log-body">
          <p class="dup-log-text">{{ row.text }}</p>
        </div>
        <button
          type="button"
          class="btn btn-secondary btn-small"
          :aria-disabled="row.lifting || undefined"
          @click="row.lifting ? undefined : store.offerAgain(row.oneId, row.otherId)"
        >{{ row.lifting ? 'Offering…' : 'Offer again' }}</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.dup-heading {
  font-size: var(--text-lg);
  font-weight: 600;
}

.dup-subheading {
  margin-top: var(--space-5);
  margin-bottom: var(--space-1);
  font-size: var(--text-md);
  font-weight: 600;
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

.dup-log-blocked {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-warning);
}
</style>
