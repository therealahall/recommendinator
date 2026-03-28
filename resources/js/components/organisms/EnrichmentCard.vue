<script setup lang="ts">
import { ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { truncate } from '@/utils/format'
import TypePills from '@/components/atoms/TypePills.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'

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
      {{ data.enrichmentJob.current_item ? truncate(data.enrichmentJob.current_item, 50) : 'Processing...' }}
      ({{ data.enrichmentJob.items_processed }}/{{ data.enrichmentJob.total_items }}
      - {{ Math.round(data.enrichmentJob.progress_percent) }}%)
    </div>

    <div class="enrichment-actions">
      <TypePills v-model="enrichType" />
      <ToggleSwitch v-model="retryNotFound" label="Retry Not Found" />
      <div class="enrichment-buttons">
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
      </div>
    </div>
  </div>
</template>

<style scoped>
.enrichment-actions {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  align-items: center;
  margin-top: var(--space-4);
}

.enrichment-buttons {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
}

.enrichment-status {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.enrichment-summary {
  margin-bottom: var(--space-3);
}
</style>
