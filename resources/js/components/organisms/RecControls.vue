<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'
import TypePills from '@/components/atoms/TypePills.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import NumberStepper from '@/components/atoms/NumberStepper.vue'

const recs = useRecommendationsStore()
const app = useAppStore()

function onGenerate(): void {
  if (recs.loading) return
  recs.fetch()
}
</script>

<template>
  <div class="card">
    <div class="toolbar rec-toolbar">
      <TypePills v-model="recs.contentType" class="rec-pills" />

      <TypeSelect v-model="recs.contentType" class="field toolbar-select rec-type-select" />

      <NumberStepper
        v-model="recs.count"
        :min="1"
        :max="app.recommendationsConfig?.max_count"
        class="rec-stepper"
        aria-label="Number of recommendations"
      />

      <div class="toolbar-divider" />

      <div class="toolbar-zone toolbar-actions">
        <button
          class="btn btn-primary"
          data-testid="generate-btn"
          :aria-disabled="recs.loading || undefined"
          @click="onGenerate"
        >{{ recs.loading ? 'Recommending…' : 'Recommend' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
