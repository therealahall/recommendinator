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
    <div class="rec-toolbar">
      <TypePills v-model="recs.contentType" :include-all="false" />

      <div class="toolbar-divider" />

      <NumberStepper
        v-model="recs.count"
        :min="1"
        :max="app.recommendationsConfig.max_count"
        aria-label="Number of recommendations"
      />

      <div class="toolbar-divider" />

      <div class="toolbar-zone toolbar-right">
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
  </div>
</template>

<style scoped>
.rec-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.toolbar-zone {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.toolbar-right {
  margin-left: auto;
}
</style>
