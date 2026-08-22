<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useDataStore } from '@/stores/data'
import { domId } from '@/utils/format'
import type { SyncIntervalOption } from '@/types/api'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const SAVED_STATUS_MS = 2500

const props = defineProps<{
  sourceId: string
  sourceName: string
  interval: string
  options: SyncIntervalOption[]
}>()

const data = useDataStore()
const status = ref<SaveStatus>('idle')
const error = ref('')
let statusTimer: ReturnType<typeof setTimeout> | null = null
let pending: string | null = null

function clearStatusTimer(): void {
  if (statusTimer) clearTimeout(statusTimer)
  statusTimer = null
}

onBeforeUnmount(clearStatusTimer)

// Vue re-patches the DOM value from the prop on every render (vuejs/core#1471),
// so binding it straight snaps the select back for the length of the save.
const selected = ref(props.interval)
watch([() => props.interval, status], () => {
  // The prop is what the server has, stale only while a save is in flight.
  if (status.value !== 'saving') selected.value = props.interval
})

// Arrow-keying a closed <select> fires a change per keystroke, outrunning the
// save, so the last one of a burst is queued rather than dropped.
async function save(next: string): Promise<void> {
  pending = next
  if (status.value === 'saving') return
  clearStatusTimer()
  status.value = 'saving'
  error.value = ''
  try {
    while (pending !== null) {
      const interval = pending
      pending = null
      await data.setSourceSchedule(props.sourceId, interval)
    }
    status.value = 'saved'
    statusTimer = setTimeout(() => {
      status.value = 'idle'
      statusTimer = null
    }, SAVED_STATUS_MS)
  } catch (err) {
    pending = null
    status.value = 'error'
    error.value = err instanceof Error ? err.message : 'Unknown error'
  }
}

function onChange(event: Event): void {
  selected.value = (event.target as HTMLSelectElement).value
  void save(selected.value)
}

const selectId = computed(() => domId('sync-cadence', props.sourceId))
const statusId = computed(() => domId('sync-cadence-status', props.sourceId))
const label = computed(() => `Automatic sync for ${props.sourceName}`)
const failed = computed(() => status.value === 'error')
// 'saved' exists to be announced: clearing a live region announces nothing.
const statusText = computed(() => {
  if (failed.value) return `Error: ${error.value}`
  if (status.value === 'saving') return 'Saving the cadence…'
  return status.value === 'saved' ? 'Cadence saved.' : ''
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
