<script setup lang="ts">
import { usePreferencesStore } from '@/stores/preferences'
import LengthPrefs from '@/components/organisms/LengthPrefs.vue'

const prefs = usePreferencesStore()
</script>

<template>
  <div class="pref-section">
    <h3>Rules</h3>
    <div class="toggle-row">
      <input type="checkbox" id="prefSeriesOrder" v-model="prefs.seriesInOrder">
      <label for="prefSeriesOrder">Recommend series in order</label>
    </div>
    <LengthPrefs />
  </div>
</template>
