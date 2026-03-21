<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'

const recs = useRecommendationsStore()
const app = useAppStore()
</script>

<template>
  <div class="card">
    <div class="rec-controls">
      <div class="form-group flex-1">
        <label for="recType">Content Type</label>
        <select id="recType" v-model="recs.contentType">
          <option value="book">Book</option>
          <option value="movie">Movie</option>
          <option value="tv_show">TV Show</option>
          <option value="video_game">Video Game</option>
        </select>
      </div>
      <div class="form-group">
        <label for="recCount">Count</label>
        <input
          type="number"
          id="recCount"
          v-model.number="recs.count"
          min="1"
          :max="app.recommendationsConfig.max_count"
        >
      </div>
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
  align-items: flex-end;
  flex-wrap: wrap;
}

.flex-1 {
  flex: 1;
}

.form-group input[type="number"] {
  width: 70px;
}
</style>
