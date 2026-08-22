<script setup lang="ts">
import { computed, nextTick, ref, useId } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import { ENRICHMENT_PROVIDERS, PROVIDER_LABELS } from '@/constants/enrichment'

const props = defineProps<{
  /** Human-readable content-type scope, '' for every type. */
  typeLabel: string
  /** Items the reset would re-queue, or null when the scope cannot be counted. */
  affected: number | null
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

// A count only where the filters let the stats answer exactly. A guess here is
// worse than no number: it is the figure the user decides against.
const question = computed(() => {
  const many = props.affected === null ? 'every matching item' : `${props.affected} item(s)`
  return (
    `Re-queue ${many} for enrichment (${scope.value})? Their current genres, ` +
    'tags and descriptions are dropped and fetched again from rate-limited APIs.'
  )
})

async function cancel(): Promise<void> {
  confirming.value = false
  await nextTick()
  document.getElementById(`${selectId}-btn`)?.focus()
}

function confirm(): void {
  confirming.value = false
  emit('reset', provider.value)
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
      :disabled="busy"
      @click="confirming = true"
    >Reset enrichment</button>
  </div>

  <ConfirmPanel
    v-if="confirming"
    :message="question"
    confirm-label="Reset"
    cancel-label="Keep it"
    destructive
    @cancel="cancel"
    @confirm="confirm"
  />
</template>

<style scoped>
.enrichment-reset {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
