<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import { ENRICHMENT_PROVIDERS, PROVIDER_LABELS } from '@/constants/enrichment'

const props = defineProps<{
  /** Human-readable content-type scope, '' for every type. */
  typeLabel: string
  /** Items a reset re-queues, keyed by provider filter; null under a type filter. */
  resettable: Record<string, number> | null
  busy: boolean
}>()

const emit = defineEmits<{
  reset: [provider: string]
}>()

const provider = ref('all')
const confirming = ref(false)
const selectId = useId()

const scope = computed(() => {
  const parts = [props.typeLabel || 'every content type']
  if (provider.value !== 'all') parts.push(PROVIDER_LABELS[provider.value])
  return parts.join(', ')
})

const subject = computed(() =>
  props.resettable === null
    ? 'every matching item'
    : `${props.resettable[provider.value] ?? 0} item(s)`,
)

const question = computed(
  () =>
    `Re-queue ${subject.value} for enrichment (${scope.value})? Their current ` +
    'genres, tags and descriptions are dropped and fetched again from ' +
    'rate-limited APIs.',
)

function answer(reset: boolean): void {
  confirming.value = false
  // 'all' is the absence of a filter, not a provider name: sent through, the
  // storage layer matches it against `enrichment_provider` and finds nothing.
  if (reset) emit('reset', provider.value === 'all' ? '' : provider.value)
}
</script>

<template>
  <div class="enrichment-reset">
    <label :for="selectId" class="sr-only">Reset which provider's matches</label>
    <select :id="selectId" v-model="provider" class="toolbar-select" data-testid="reset-provider">
      <option v-for="key in ENRICHMENT_PROVIDERS" :key="key" :value="key">
        {{ PROVIDER_LABELS[key] }}
      </option>
    </select>
    <button
      :id="`${selectId}-btn`"
      type="button"
      class="btn btn-danger"
      data-testid="reset-btn"
      :aria-disabled="busy || undefined"
      @click="confirming = !busy"
    >Reset enrichment</button>
  </div>

  <ConfirmPanel
    v-if="confirming"
    :message="question"
    confirm-label="Reset"
    cancel-label="Keep it"
    destructive
    @cancel="answer(false)"
    @confirm="answer(true)"
  />
</template>

<style scoped>
.enrichment-reset {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
