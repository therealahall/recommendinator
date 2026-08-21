<script setup lang="ts">
import { computed, ref } from 'vue'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'
import type { DuplicateSide, DuplicateSuggestion } from '@/types/api'

const props = defineProps<{
  suggestion: DuplicateSuggestion
  merging: boolean
  declining: boolean
  error: string
}>()

const emit = defineEmits<{
  merge: [survivorId: number, absorbedId: number]
  decline: [oneId: number, otherId: number]
}>()

const chosen = ref<number | null>(null)

const busy = computed(() => props.merging || props.declining)

const loose = computed(() => props.suggestion.evidence === 'title_qualifier')

const typeLabel = computed(
  () =>
    CONTENT_TYPE_OPTIONS.find((one) => one.value === props.suggestion.content_type)
      ?.label ?? props.suggestion.content_type,
)

const sides = computed(() => {
  const { survivor, absorbed } = props.suggestion
  return [
    view(survivor, absorbed.db_id),
    view(absorbed, survivor.db_id),
  ]
})

function view(side: DuplicateSide, otherId: number) {
  return {
    keepId: side.db_id,
    dropId: otherId,
    title: side.title,
    creator: side.creator || 'Creator not recorded',
    source: side.source || 'Source not recorded',
    year: side.release_year,
    label:
      props.merging && chosen.value === side.db_id
        ? 'Merging…'
        : `Merge, keeping “${side.title}”`,
  }
}

function onMerge(keepId: number, dropId: number): void {
  if (busy.value) return
  chosen.value = keepId
  emit('merge', keepId, dropId)
}

function onDecline(): void {
  if (busy.value) return
  emit('decline', props.suggestion.survivor.db_id, props.suggestion.absorbed.db_id)
}
</script>

<template>
  <li class="dup-pair" :aria-busy="busy || undefined">
    <p class="dup-pair-evidence">
      <span class="badge" :class="loose ? 'dup-badge-loose' : 'dup-badge-exact'">
        {{ suggestion.evidence_label }}
      </span>
      <span class="badge dup-badge-type">{{ typeLabel }}</span>
    </p>

    <p v-if="loose" class="dup-pair-caution">
      Only the looser key matched, dropping a trailing parenthetical — look
      twice before merging.
    </p>

    <div class="dup-pair-sides">
      <div v-for="side in sides" :key="side.keepId" class="dup-side">
        <p class="dup-side-title">{{ side.title }}</p>
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
        <button
          type="button"
          class="btn btn-secondary dup-side-keep"
          :aria-disabled="busy || undefined"
          @click="onMerge(side.keepId, side.dropId)"
        >{{ side.label }}</button>
      </div>
    </div>

    <p class="dup-pair-actions">
      <span v-if="error" class="dup-pair-error">{{ error }}</span>
      <button
        type="button"
        class="btn btn-ghost"
        :aria-disabled="busy || undefined"
        @click="onDecline"
      >{{ declining ? 'Dismissing…' : 'Not duplicates' }}</button>
    </p>
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

/* Not the shared .badge-type: --accent-light on its accent wash is 4.06:1 on
   the plain card and less on this one. The type is the quieter of the two
   labels here, so it takes a text token and the evidence keeps the colour. */
.dup-badge-type {
  background: transparent;
  color: var(--text-secondary);
  border-color: var(--border-default);
}

.dup-badge-exact {
  background: var(--bg-card);
  color: var(--text-secondary);
  border-color: var(--border-default);
}

/* The looser key is a weaker claim, so it reads as one before it is acted on.
   The wording carries the difference on its own; the colour only echoes it. */
.dup-badge-loose {
  background: transparent;
  color: var(--color-warning);
  border-color: color-mix(in srgb, var(--color-warning) 55%, transparent);
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
  font-weight: 600;
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

.dup-pair-actions {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  justify-content: flex-end;
  margin-top: var(--space-3);
}

.dup-pair-error {
  margin-right: auto;
  font-size: var(--text-sm);
  color: var(--color-warning);
}
</style>
