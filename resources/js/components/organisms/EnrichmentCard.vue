<script setup lang="ts">
import { ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { truncate } from '@/utils/format'
import TypePills from '@/components/atoms/TypePills.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'

const data = useDataStore()
const enrichType = ref('')
const retryNotFound = ref(false)
const resetMode = ref(false)

function runEnrichment() {
  if (resetMode.value) {
    data.resetEnrichment(enrichType.value || undefined)
  } else {
    data.startEnrichment(enrichType.value || undefined, retryNotFound.value)
  }
}
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

    <div class="enrichment-toolbar">
      <TypePills v-model="enrichType" />

      <div class="toolbar-divider" />

      <div class="toolbar-zone">
        <ToggleSwitch v-model="retryNotFound" label="Retry Not Found" />
        <ToggleSwitch v-model="resetMode" label="Reset Enrichment" />
      </div>

      <div class="toolbar-divider" />

      <div class="toolbar-right">
        <button
          class="btn"
          :class="resetMode ? 'btn-warning' : 'btn-primary'"
          :disabled="data.enrichmentJob?.running"
          @click="runEnrichment"
        >{{ resetMode ? 'Reset Enrichment' : 'Enrich' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.enrichment-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.toolbar-zone {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.toolbar-right {
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
