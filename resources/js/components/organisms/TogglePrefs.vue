<script setup lang="ts">
import { usePreferencesStore } from '@/stores/preferences'

const prefs = usePreferencesStore()
</script>

<template>
  <div class="pref-section">
    <h3>Toggles</h3>
    <div class="toggle-row">
      <input type="checkbox" id="prefSeriesOrder" v-model="prefs.seriesInOrder">
      <label for="prefSeriesOrder">Recommend series in order</label>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="prefVariety" v-model="prefs.varietyAfterCompletion">
      <label for="prefVariety">Variety after completion</label>
    </div>
  </div>
</template>
