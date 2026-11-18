<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import type { EnrichmentProvider } from '@/types/api'
import { resetQuestion } from '@/utils/enrichmentReset'

const props = defineProps<{
  /** Human-readable content-type scope, '' for every type. */
  typeLabel: string
  /** The installed providers a reset can be narrowed to, as the API reports them. */
  providers: EnrichmentProvider[]
  /** Items a reset re-queues, keyed by provider filter; null under a type filter. */
  resettable: Record<string, number> | null
  legacy: Record<string, number> | null
  busy: boolean
}>()

const emit = defineEmits<{
  reset: [provider: string, hard: boolean]
}>()

// Offered beside the installed providers, and deliberately not one of them:
// 'all' is the absence of a filter.
const EVERY_PROVIDER: EnrichmentProvider = { name: 'all', display_name: 'All providers' }

const provider = ref(EVERY_PROVIDER.name)
const hard = ref(false)
const confirming = ref(false)
const selectId = useId()

const choices = computed(() => [EVERY_PROVIDER, ...props.providers])

const scope = computed(() => {
  const parts = [props.typeLabel || 'every content type']
  const chosen = props.providers.find((one) => one.name === provider.value)
  if (chosen) parts.push(chosen.display_name)
  return parts.join(', ')
})

const subject = computed(() =>
  props.resettable === null
    ? 'every matching item'
    : `${props.resettable[provider.value] ?? 0} item(s)`,
)

const legacySubject = computed(() =>
  props.legacy === null
    ? 'wherever a matching item holds one'
    : `held by at least ${props.legacy[provider.value] ?? 0} item(s)`,
)

const question = computed(() =>
  resetQuestion(
    `Re-queue ${subject.value} for enrichment (${scope.value})?`,
    legacySubject.value,
    hard.value,
  ),
)

function answer(reset: boolean): void {
  confirming.value = false
  // Sent through, the storage layer would match 'all' against
  // `enrichment_provider` and find nothing.
  if (reset)
    emit('reset', provider.value === EVERY_PROVIDER.name ? '' : provider.value, hard.value)
}
</script>

<template>
  <div class="enrichment-reset">
    <label :for="selectId" class="sr-only">Reset which provider's values</label>
    <select :id="selectId" v-model="provider" class="field toolbar-select" data-testid="reset-provider">
      <option v-for="one in choices" :key="one.name" :value="one.name">
        {{ one.display_name }}
      </option>
    </select>
    <ToggleSwitch v-model="hard" label="Drop unclaimed values too" />
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
/* Unwrapped, the three controls are one unbreakable width wider than a phone,
   and the toolbar wrapping around the group cannot shrink it. */
.enrichment-reset {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
