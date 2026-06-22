<script setup lang="ts">
import { usePreferencesStore, VARIETY_PENALTY_TOOLTIP } from '@/stores/preferences'
import VarietySlider from '@/components/atoms/VarietySlider.vue'

const prefs = usePreferencesStore()
</script>

<template>
  <div class="pref-section">
    <h3>Toggles</h3>
    <div class="toggle-row">
      <input type="checkbox" id="prefSeriesOrder" v-model="prefs.seriesInOrder">
      <label for="prefSeriesOrder">Recommend series in order</label>
    </div>
    <VarietySlider
      label="Variety after completion"
      :tooltip="VARIETY_PENALTY_TOOLTIP"
      :model-value="prefs.varietyPenalty"
      @update:model-value="prefs.varietyPenalty = $event"
    />
  </div>
</template>
