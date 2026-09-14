<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { SAVED_STATUS_MS, type SaveStatus } from '@/composables/useSecretStatus'
import { useDataStore } from '@/stores/data'
import { domId } from '@/utils/format'

const props = defineProps<{
  sourceId: string
  sourceName: string
}>()

const data = useDataStore()
const name = ref(props.sourceName)
const status = ref<SaveStatus>('idle')
const error = ref('')
let statusTimer: ReturnType<typeof setTimeout> | null = null

function clearStatusTimer(): void {
  if (statusTimer) clearTimeout(statusTimer)
  statusTimer = null
}

onBeforeUnmount(clearStatusTimer)

// A cleared name comes back as the plugin's, so a save takes the server's word;
// otherwise a listing refresh must not wipe a name still being typed.
watch(
  () => props.sourceName,
  (next, previous) => {
    if (status.value === 'saving' || name.value === previous) name.value = next
  },
)

async function onSubmit(): Promise<void> {
  if (status.value === 'saving') return
  clearStatusTimer()
  status.value = 'saving'
  error.value = ''
  try {
    await data.setSourceDisplayName(props.sourceId, name.value)
    status.value = 'saved'
    statusTimer = setTimeout(() => {
      status.value = 'idle'
      statusTimer = null
    }, SAVED_STATUS_MS)
  } catch (err) {
    status.value = 'error'
    error.value = err instanceof Error ? err.message : 'Unknown error'
  }
}

const inputId = computed(() => domId('source-name', props.sourceId))
const statusId = computed(() => domId('source-name-status', props.sourceId))
const failed = computed(() => status.value === 'error')
const statusText = computed(() => {
  if (failed.value) return `Error: ${error.value}`
  if (status.value === 'saving') return 'Saving the name…'
  return status.value === 'saved' ? 'Name saved.' : ''
})
</script>

<template>
  <form
    class="source-form-field source-name-field"
    :data-testid="`source-name-form-${sourceId}`"
    @submit.prevent="onSubmit"
  >
    <label :for="inputId" class="source-form-label">Name</label>
    <div class="source-name-row">
      <input
        :id="inputId"
        v-model="name"
        type="text"
        class="field"
        :data-testid="`source-name-input-${sourceId}`"
        :aria-describedby="statusText ? statusId : undefined"
      />
      <button
        type="submit"
        class="btn btn-secondary"
        :aria-disabled="status === 'saving' || undefined"
      >{{ status === 'saving' ? 'Saving…' : 'Save name' }}</button>
    </div>
    <!-- Mounted before it has anything to say: a region inserted already
         populated is read as page content, not as a status (WCAG 4.1.3). -->
    <p
      :id="statusId"
      class="auth-status"
      :class="{ failed }"
      :role="failed ? 'alert' : 'status'"
      :aria-live="failed ? 'assertive' : 'polite'"
      aria-atomic="true"
    >{{ statusText }}</p>
  </form>
</template>

<style scoped>
.source-name-field {
  margin-bottom: var(--space-4);
}

.source-name-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.source-name-row .field {
  flex: 1 1 12rem;
}
</style>
