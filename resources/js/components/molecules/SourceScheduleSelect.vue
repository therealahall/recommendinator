<script setup lang="ts">
import { computed } from 'vue'
import { domId } from '@/utils/format'
import type { SyncIntervalOption } from '@/types/api'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

const props = defineProps<{
  sourceId: string
  sourceName: string
  interval: string
  /** Straight from the schema response: no interface retypes the presets. */
  options: SyncIntervalOption[]
  status: SaveStatus
  error: string
}>()

const emit = defineEmits<{
  change: [interval: string]
}>()

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
      :value="interval"
      :aria-describedby="statusText ? statusId : undefined"
      @change="emit('change', ($event.target as HTMLSelectElement).value)"
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
/* .source-form-field/-label and .toolbar-select are base.css primitives. */
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
