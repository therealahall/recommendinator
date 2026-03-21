<script setup lang="ts">
import { usePreferencesStore, SCORER_KEYS, SCORER_TOOLTIPS } from '@/stores/preferences'
import { useAppStore } from '@/stores/app'
import { formatScorerName } from '@/utils/format'
import ScorerSlider from '@/components/atoms/ScorerSlider.vue'

const prefs = usePreferencesStore()
const app = useAppStore()

function shouldShow(key: string): boolean {
  if (key === 'semantic_similarity') {
    return app.features.ai_enabled && app.features.embeddings_enabled
  }
  return true
}
</script>

<template>
  <div class="pref-section">
    <h3>Scorer Weights</h3>
    <template v-for="key in SCORER_KEYS" :key="key">
      <ScorerSlider
        v-if="shouldShow(key)"
        :label="formatScorerName(key)"
        :tooltip="SCORER_TOOLTIPS[key]"
        :model-value="prefs.getWeight(key)"
        @update:model-value="prefs.setWeight(key, $event)"
      />
    </template>
  </div>
</template>
