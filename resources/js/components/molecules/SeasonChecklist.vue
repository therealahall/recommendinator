<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'

const props = defineProps<{
  totalSeasons: number
  modelValue: number[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number[]]
}>()

const watchedSet = computed(() => new Set(props.modelValue))

// Select All, Deselect All and a status change in the parent all rewrite the
// whole checklist, and this counter is the only thing that reports the result —
// so it announces rather than merely displays (WCAG 4.1.3).
const watchedLabel = computed(() => {
  const noun = props.totalSeasons === 1 ? 'season' : 'seasons'
  if (props.modelValue.length === 0) return `No ${noun} watched`
  return `${props.modelValue.length} of ${props.totalSeasons} ${noun} watched`
})

function toggle(season: number) {
  const current = new Set(props.modelValue)
  if (current.has(season)) {
    current.delete(season)
  } else {
    current.add(season)
  }
  emit('update:modelValue', Array.from(current).sort((a, b) => a - b))
}

function selectAll() {
  const all = Array.from({ length: props.totalSeasons }, (_, i) => i + 1)
  emit('update:modelValue', all)
}

function deselectAll() {
  emit('update:modelValue', [])
}
</script>

<template>
  <div>
    <div class="season-controls">
      <button class="btn btn-small btn-secondary" type="button" @click="selectAll">Select All</button>
      <button class="btn btn-small btn-secondary" type="button" @click="deselectAll">Deselect All</button>
      <span
        class="season-counter"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >{{ watchedLabel }}</span>
    </div>
    <div class="season-grid" role="group" aria-label="Seasons watched">
      <label
        v-for="season in totalSeasons"
        :key="season"
        class="season-checkbox"
        :class="{ checked: watchedSet.has(season) }"
      >
        <input
          type="checkbox"
          class="sr-only"
          :checked="watchedSet.has(season)"
          @change="toggle(season)"
        >
        <AppIcon v-if="watchedSet.has(season)" name="check" />
        {{ season }}
      </label>
    </div>
  </div>
</template>

<style scoped>
.season-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.season-counter {
  font-size: var(--text-sm);
  color: var(--text-muted);
  margin-left: auto;
}

.season-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.season-checkbox {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  background: var(--bg-elevated);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.season-checkbox:has(:focus-visible) {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* The season number is exposed text on this tint and owes 4.5:1, where the
   check glyph beside it is a graphical object owing 3:1 — --accent-hover was
   enough for the glyph and short for the number. */
.season-checkbox.checked {
  background: color-mix(in srgb, var(--accent) 20%, transparent);
  border-color: var(--accent);
  color: var(--text-primary);
}

</style>
