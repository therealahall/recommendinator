<script setup lang="ts">
import {
  usePreferencesStore,
  SCORER_KEYS,
  SCORER_TOOLTIPS,
  VARIETY_PENALTY_TOOLTIP,
} from '@/stores/preferences'
import { formatScorerName } from '@/utils/format'
import ScorerSlider from '@/components/atoms/ScorerSlider.vue'

const prefs = usePreferencesStore()
</script>

<template>
  <div class="pref-section">
    <h3>Scoring</h3>
    <ScorerSlider
      v-for="key in SCORER_KEYS"
      :key="key"
      :label="formatScorerName(key)"
      :tooltip="SCORER_TOOLTIPS[key]"
      :model-value="prefs.getWeight(key)"
      @update:model-value="prefs.setWeight(key, $event)"
    />
    <ScorerSlider
      label="Variety After Completion"
      :tooltip="VARIETY_PENALTY_TOOLTIP"
      :model-value="prefs.varietyPenalty"
      @update:model-value="prefs.varietyPenalty = $event"
    />
  </div>
</template>
