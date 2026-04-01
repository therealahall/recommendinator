<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'
import TypePills from '@/components/atoms/TypePills.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import NumberStepper from '@/components/atoms/NumberStepper.vue'

const recs = useRecommendationsStore()
const app = useAppStore()
</script>

<template>
  <div class="card">
    <div class="rec-toolbar">
      <!-- Desktop: Type pills; Mobile: Type dropdown -->
      <TypePills v-model="recs.contentType" :include-all="false" class="rec-pills" />
      <TypeSelect v-model="recs.contentType" :include-all="false" class="toolbar-select rec-type-select" />

      <div class="toolbar-divider" />

      <!-- Stepper + action buttons (mobile: row 2; desktop: inline in toolbar) -->
      <div class="rec-actions-row">
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

.rec-actions-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.toolbar-right {
  margin-left: auto;
}

.rec-type-select {
  display: none;
}

/* Mobile: dropdown replaces pills, dividers hidden */
@media (max-width: 640px) {
  .rec-pills,
  .rec-toolbar > .toolbar-divider {
    display: none;
  }

  .rec-actions-row {
    width: 100%;
    gap: var(--space-2);
  }

  .rec-actions-row > .toolbar-divider {
    display: none;
  }

  .rec-type-select {
    display: block;
    width: 100%;
  }

  .toolbar-right {
    margin-left: 0;
  }
}
</style>
