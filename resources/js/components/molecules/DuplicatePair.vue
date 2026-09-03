<script setup lang="ts">
import { computed, ref } from 'vue'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'
import type { DuplicateSide, DuplicateSuggestion } from '@/types/api'

const props = defineProps<{
  suggestion: DuplicateSuggestion
  merging: boolean
  declining: boolean
}>()

const emit = defineEmits<{
  merge: [survivorId: number, absorbedIds: number[]]
  decline: [copyId: number, otherIds: number[]]
}>()

const chosen = ref<number | null>(null)

const busy = computed(() => props.merging || props.declining)

const loose = computed(() => props.suggestion.evidence === 'title_qualifier')

const typeLabel = computed(
  () =>
    CONTENT_TYPE_OPTIONS.find((one) => one.value === props.suggestion.content_type)
      ?.label ?? props.suggestion.content_type,
)

const sides = computed(() => props.suggestion.copies.map(view))

function others(keepId: number): number[] {
  return props.suggestion.copies
    .map((copy) => copy.db_id)
    .filter((id) => id !== keepId)
}

function view(side: DuplicateSide) {
  const proposed = side.db_id === props.suggestion.survivor_id
  // Copies of one work often share a title, so a button names its row (4.1.2).
  const named = `“${side.title}” from ${
    side.source || 'source not recorded'
  }, row ${side.db_id}`
  return {
    keepId: side.db_id,
    dropIds: others(side.db_id),
    proposed,
    elsewhere: side.also_offered,
    title: side.title,
    creator: side.creator || 'Creator not recorded',
    source: side.source || 'Source not recorded',
    year: side.release_year,
    label:
      props.merging && chosen.value === side.db_id
        ? 'Merging…'
        // The Suggested badge is sighted-only, so without this the two names differ by row id alone (4.1.2).
        : `Merge, keeping ${named}${proposed ? ', suggested to keep' : ''}`,
    apart:
      props.declining && chosen.value === side.db_id
        ? 'Dismissing…'
        : `${named} is not the same work`,
  }
}

function onMerge(keepId: number, dropIds: number[]): void {
  if (busy.value) return
  chosen.value = keepId
  emit('merge', keepId, dropIds)
}

function onDecline(copyId: number, otherIds: number[]): void {
  if (busy.value) return
  chosen.value = copyId
  emit('decline', copyId, otherIds)
}
</script>

<template>
  <li class="dup-pair" :aria-busy="busy || undefined">
    <p class="dup-pair-evidence">
      <span class="badge" :data-tone="loose ? 'warning' : undefined">
        {{ suggestion.evidence_label }}
      </span>
      <span class="badge">{{ typeLabel }}</span>
    </p>

    <p v-if="loose" class="dup-pair-caution">
      Only the looser key matched, dropping a trailing parenthetical — look
      twice before merging.
    </p>

    <div class="dup-pair-sides">
      <div v-for="side in sides" :key="side.keepId" class="dup-side">
        <p class="dup-side-title">{{ side.title }}</p>
        <span
          v-if="side.proposed"
          class="badge dup-side-proposed"
          data-tone="accent"
        >Suggested</span>
        <p class="dup-side-meta">
          <span>{{ side.creator }}</span>
          <span aria-hidden="true">·</span>
          <span>{{ side.source }}</span>
          <template v-if="side.year">
            <span aria-hidden="true">·</span>
            <span>{{ side.year }}</span>
          </template>
          <span aria-hidden="true">·</span>
          <span>row {{ side.keepId }}</span>
        </p>
        <p v-if="side.elsewhere" class="dup-side-elsewhere">{{ side.elsewhere }}</p>
        <button
          type="button"
          class="btn btn-secondary dup-side-keep"
          :aria-disabled="busy || undefined"
          @click="onMerge(side.keepId, side.dropIds)"
        >{{ side.label }}</button>
        <button
          type="button"
          class="btn btn-ghost dup-side-apart"
          :aria-disabled="busy || undefined"
          @click="onDecline(side.keepId, side.dropIds)"
        >{{ side.apart }}</button>
      </div>
    </div>
  </li>
</template>

<style scoped>
.dup-pair {
  list-style: none;
  padding: var(--space-4);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-elevated);
}

.dup-pair-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.dup-pair-caution {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-warning);
}

.dup-pair-sides {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.dup-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.dup-side-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.dup-side-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.dup-side-keep {
  margin-top: auto;
  text-align: left;
  /* The label carries the whole title, which a phone cannot fit on one line,
     and shortening it would take the survivor out of the accessible name. */
  white-space: normal;
  overflow-wrap: anywhere;
}

.dup-side-proposed {
  align-self: flex-start;
}

.dup-side-elsewhere {
  font-size: var(--text-sm);
  color: var(--color-warning);
}

.dup-side-apart {
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
