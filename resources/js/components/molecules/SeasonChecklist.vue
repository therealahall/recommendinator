<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  totalSeasons: number
  modelValue: number[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number[]]
}>()

const watchedSet = computed(() => new Set(props.modelValue))
const watchedCount = computed(() => props.modelValue.length)

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
      <span class="season-counter">{{ watchedCount }} / {{ totalSeasons }}</span>
    </div>
    <div class="season-grid">
      <label
        v-for="season in totalSeasons"
        :key="season"
        class="season-checkbox"
        :class="{ checked: watchedSet.has(season) }"
      >
        <input
          type="checkbox"
          :checked="watchedSet.has(season)"
          @change="toggle(season)"
        >
        {{ season }}
      </label>
    </div>
  </div>
</template>

<style scoped>
.season-controls {
  display: flex;
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
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.season-checkbox.checked {
  background: color-mix(in srgb, var(--accent) 20%, transparent);
  border-color: var(--accent);
  color: var(--accent-light);
}

.season-checkbox input {
  display: none;
}
</style>
