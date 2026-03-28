<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'
import TypePills from '@/components/atoms/TypePills.vue'

const recs = useRecommendationsStore()
const app = useAppStore()
</script>

<template>
  <div class="card">
    <div class="rec-controls">
      <TypePills v-model="recs.contentType" :include-all="false" />
      <input
        type="number"
        id="recCount"
        class="count-input"
        v-model.number="recs.count"
        min="1"
        :max="app.recommendationsConfig.max_count"
        title="Number of recommendations"
      >
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

.count-input {
  width: 60px;
  padding: var(--space-2) var(--space-2);
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-family: inherit;
  text-align: center;
}

.count-input:focus {
  outline: none;
  border-color: var(--border-focus);
}
</style>
