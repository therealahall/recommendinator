<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'
import TypePills from '@/components/atoms/TypePills.vue'
import NumberStepper from '@/components/atoms/NumberStepper.vue'

const recs = useRecommendationsStore()
const app = useAppStore()
</script>

<template>
  <div class="card">
    <div class="rec-controls">
      <TypePills v-model="recs.contentType" :include-all="false" />
      <NumberStepper
        v-model="recs.count"
        :min="1"
        :max="app.recommendationsConfig.max_count"
        aria-label="Number of recommendations"
      />
      <button
        class="btn btn-secondary"
        :disabled="recs.loading"
        @click="recs.fetch(false)"
      >Generate</button>
      <button
        v-if="app.aiReasoningEnabled"
        class="btn btn-primary"
        :disabled="recs.loading"
        @click="recs.fetch(true)"
      >&#10024; AI Recommendations</button>
    </div>
  </div>
</template>

<style scoped>
.rec-controls {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
</style>
