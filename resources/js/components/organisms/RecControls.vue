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

      <!-- Mobile: dropdown replaces pills -->
      <TypeSelect v-model="recs.contentType" :include-all="false" class="toolbar-select rec-type-select" />

      <NumberStepper
        v-model="recs.count"
        :min="1"
        :max="app.recommendationsConfig.max_count"
        class="rec-stepper"
        aria-label="Number of recommendations"
      />

      <div class="toolbar-divider" />

      <div class="toolbar-zone toolbar-actions">
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
        ><span aria-hidden="true">&#10024;</span> AI Recommendations</button>
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

.toolbar-actions {
  margin-left: auto;
}

.rec-type-select {
  display: none;
}

/* Mobile: dropdown replaces pills; dropdown + stepper share top row;
   action buttons wrap to their own full-width row below. */
@media (max-width: 640px) {
  .rec-pills,
  .rec-toolbar > .toolbar-divider {
    display: none;
  }

  .rec-type-select {
    display: block;
    flex: 1 1 0;
    min-width: 0;
  }

  .rec-stepper {
    flex: 0 0 auto;
  }

  .toolbar-actions {
    width: 100%;
    margin-left: 0;
  }

  .toolbar-actions .btn {
    flex: 1 1 0;
  }
}
</style>
