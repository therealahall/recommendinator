<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'
import WeightsDialog from '@/components/organisms/WeightsDialog.vue'
import { DEFAULT_WEIGHTS, SCORER_KEYS, usePreferencesStore } from '@/stores/preferences'

const prefs = usePreferencesStore()
const open = ref(false)

const changed = computed(
  () =>
    SCORER_KEYS.filter((key) => prefs.getWeight(key) !== DEFAULT_WEIGHTS[key]).length +
    (prefs.varietyPenalty === 0 ? 0 : 1),
)

/** Closed, the control still says what it holds: a button reading only
 *  "Weights" leaves the screen incomplete rather than complete-without-it. */
const summary = computed(() => {
  if (!prefs.hasLoaded) return `${SCORER_KEYS.length + 1} signals`
  if (changed.value === 0) return 'all at their defaults'
  return `${changed.value} changed`
})

// Fetched by the trigger, not the panel: the closed summary is a claim about
// stored preferences, and unloaded it would be a guess.
onMounted(() => {
  if (!prefs.hasLoaded && !prefs.loading) prefs.load()
})
</script>

<template>
  <button
    type="button"
    class="btn btn-secondary weights-trigger"
    aria-haspopup="dialog"
    :aria-expanded="open"
    :aria-controls="open ? 'weights-panel' : undefined"
    data-testid="weights-trigger"
    @click="open = true"
  >
    <AppIcon name="sliders" />
    Scoring weights
    <!-- The tone is a second channel behind the count, never the carrier: the
         badge says which state it is in either way (WCAG 1.4.1). -->
    <span class="badge" :data-tone="prefs.hasLoaded && changed > 0 ? 'accent' : undefined">
      {{ summary }}
    </span>
  </button>

  <WeightsDialog v-if="open" :summary="summary" @close="open = false" />
</template>

<style scoped>
.weights-trigger {
  min-height: 44px;
}
</style>
