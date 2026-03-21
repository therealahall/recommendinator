<script setup lang="ts">
import { ref } from 'vue'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const enrichType = ref('')
const retryNotFound = ref(false)
</script>

<template>
  <div v-if="data.enrichmentEnabled" class="card">
    <h2>Metadata Enrichment</h2>
    <p class="help-text">Enrichment adds genres, tags, and descriptions from external APIs (TMDB, OpenLibrary, RAWG).</p>

    <div v-if="data.enrichmentStats">
      <div v-if="data.enrichmentStats.total === 0" class="empty-state">
        No items to enrich. Sync some content first.
      </div>
      <template v-else>
        <div class="enrichment-summary">
          <div class="enrichment-progress-row">
            <span>{{ data.enrichmentStats.enriched }}/{{ data.enrichmentStats.total }}
              ({{ Math.round((data.enrichmentStats.enriched / data.enrichmentStats.total) * 100) }}% enriched)</span>
          </div>
        </div>
      </template>
    </div>

    <!-- Job progress -->
    <div v-if="data.enrichmentJob?.running" class="enrichment-status">
      <span class="spinner" />
      {{ data.enrichmentJob.current_item || 'Processing...' }}
      ({{ data.enrichmentJob.items_processed }}/{{ data.enrichmentJob.total_items }}
      - {{ Math.round(data.enrichmentJob.progress_percent) }}%)
    </div>

    <div class="enrichment-actions">
      <button
        class="btn btn-primary"
        :disabled="data.enrichmentJob?.running"
        @click="data.startEnrichment(enrichType || undefined, retryNotFound)"
      >Enrich</button>
      <button
        class="btn btn-warning"
        :disabled="data.enrichmentJob?.running"
        @click="data.resetEnrichment(enrichType || undefined)"
      >Reset & Re-enrich</button>
      <select v-model="enrichType">
        <option value="">All Types</option>
        <option value="book">Books</option>
        <option value="movie">Movies</option>
        <option value="tv_show">TV Shows</option>
        <option value="video_game">Video Games</option>
      </select>
      <label class="checkbox-label">
        <input type="checkbox" v-model="retryNotFound"> Retry Not Found
      </label>
    </div>
  </div>
</template>

<style scoped>
.enrichment-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  align-items: center;
  margin-top: var(--space-4);
}

.enrichment-status {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.enrichment-summary {
  margin-bottom: var(--space-3);
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
}
</style>
