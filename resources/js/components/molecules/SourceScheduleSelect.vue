<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { domId } from '@/utils/format'
import type { SyncIntervalOption } from '@/types/api'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

const props = defineProps<{
  sourceId: string
  sourceName: string
  interval: string
  options: SyncIntervalOption[]
  status: SaveStatus
  error: string
}>()

const emit = defineEmits<{
  change: [interval: string]
}>()

// Vue re-patches the DOM value from the prop on every render (vuejs/core#1471),
// so binding it straight snaps the select back for the length of the save.
const selected = ref(props.interval)
watch([() => props.interval, () => props.status], () => {
  // The prop is what the server has, stale only while a save is in flight.
  if (props.status !== 'saving') selected.value = props.interval
})

function onChange(event: Event): void {
  selected.value = (event.target as HTMLSelectElement).value
  emit('change', selected.value)
}

const selectId = computed(() => domId('sync-cadence', props.sourceId))
const statusId = computed(() => domId('sync-cadence-status', props.sourceId))
const label = computed(() => `Automatic sync for ${props.sourceName}`)
const failed = computed(() => props.status === 'error')
// 'saved' exists to be announced: clearing a live region announces nothing.
const statusText = computed(() => {
  if (failed.value) return `Error: ${props.error}`
  if (props.status === 'saving') return 'Saving the cadence…'
  return props.status === 'saved' ? 'Cadence saved.' : ''
})
</script>

<template>
  <div class="source-form-field cadence-field">
    <label :for="selectId" class="source-form-label">{{ label }}</label>
    <select
      :id="selectId"
      class="toolbar-select cadence-select"
      :data-testid="`cadence-select-${sourceId}`"
      :value="selected"
      :aria-describedby="statusText ? statusId : undefined"
      @change="onChange"
    >
      <option v-for="option in options" :key="option.key" :value="option.key">
        {{ option.label }}
      </option>
    </select>
    <!-- Mounted before it has anything to say: a region inserted already
         populated is read as page content, not as a status (WCAG 4.1.3). -->
    <p
      :id="statusId"
      class="cadence-status"
      :class="{ 'cadence-status--error': failed }"
      :data-testid="`cadence-status-${sourceId}`"
      :role="failed ? 'alert' : 'status'"
      :aria-live="failed ? 'assertive' : 'polite'"
      aria-atomic="true"
    >{{ statusText }}</p>
  </div>
</template>

<style scoped>
.cadence-field {
  margin-bottom: var(--space-4);
}

.cadence-select {
  align-self: flex-start;
  min-width: 12rem;
}

.cadence-status {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

/* Reinforces the word "Error:" beside it rather than replacing it (1.4.1). */
.cadence-status--error {
  color: var(--color-error-text);
}
</style>
