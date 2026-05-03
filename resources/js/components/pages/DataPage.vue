<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import SyncSourceAccordion from '@/components/organisms/SyncSourceAccordion.vue'
import AddSourceModal from '@/components/organisms/AddSourceModal.vue'
import EnrichmentCard from '@/components/organisms/EnrichmentCard.vue'

const data = useDataStore()
const showAddSourceModal = ref(false)

onMounted(() => {
  data.loadSyncSources()
  data.checkSyncStatus()
  data.loadEnrichmentStats()
  data.checkEnrichmentStatus()
})

onUnmounted(() => {
  data.cleanup()
})

const syncAllLabel = computed(() => {
  if (data.syncStatus === 'running' && data.syncingSource === 'all') return 'Syncing...'
  if (data.syncStatus === 'running') return 'Sync in Progress'
  return 'Sync All Sources'
})

// Enabled sources first, disabled sources collapsed at the bottom in a
// muted state. Within each group preserve the API ordering (already
// alphabetical by source id).
const orderedSources = computed(() => {
  return [...data.syncSources].sort((a, b) => {
    if (a.enabled === b.enabled) return 0
    return a.enabled ? -1 : 1
  })
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Data</h2>
      <p class="page-description">Sync sources and enrich metadata from external APIs.</p>
    </div>

    <div class="card">
      <div class="sync-sources-header">
        <h3>Sync Sources</h3>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="add-source-btn"
          @click="showAddSourceModal = true"
        >+ Add source</button>
      </div>
      <div
        v-if="data.syncMessage"
        class="sync-status-message"
        :role="data.syncStatus === 'failed' ? 'alert' : 'status'"
        :aria-live="data.syncStatus === 'failed' ? 'assertive' : 'polite'"
        :class="{
          'sync-status-error': data.syncStatus === 'failed',
          'sync-status-success': data.syncStatus === 'completed',
          'sync-status-info': data.syncStatus === 'running' || data.syncStatus === 'idle',
        }"
      >{{ data.syncMessage }}</div>
      <div
        v-if="data.syncJob?.progress_percent != null && data.syncStatus === 'running'"
        class="sync-progress-bar"
        role="progressbar"
        :aria-valuenow="data.syncJob.progress_percent"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-label="`Sync progress: ${data.syncJob.progress_percent}%`"
      >
        <div class="sync-progress-fill" :style="{ width: `${data.syncJob.progress_percent}%` }" />
      </div>
      <p class="help-text">Sync data from your configured sources. Only one sync can run at a time.</p>

      <div v-if="data.syncLoading" class="empty-state"><span class="spinner" /> Loading sync sources...</div>
      <div v-else-if="data.syncSources.length === 0" class="empty-state">
        No sync sources configured. Add sources to config.yaml with enabled: true.
      </div>
      <div v-else class="sync-accordion-list">
        <SyncSourceAccordion
          v-for="source in orderedSources"
          :key="source.id"
          :source="source"
          :syncing="data.syncingSource === source.id || data.syncingSource === 'all'"
          :disabled="data.syncStatus === 'running' && data.syncingSource !== source.id && data.syncingSource !== 'all'"
          @sync="data.triggerSync($event)"
        />
        <div v-if="data.syncSources.length > 1" class="sync-all-card">
          <div>
            <h3>All Sources</h3>
            <p class="sync-plugin-name">Sync all enabled sources at once</p>
          </div>
          <button
            type="button"
            class="btn btn-secondary sync-btn"
            :disabled="data.syncStatus === 'running'"
            :aria-label="
              data.syncStatus === 'running' && data.syncingSource === 'all'
                ? 'Syncing all sources — in progress'
                : data.syncStatus === 'running'
                ? 'Sync all sources — another sync is in progress'
                : 'Sync all sources'
            "
            @click="data.triggerSync('all')"
          >{{ syncAllLabel }}</button>
        </div>
      </div>
    </div>

    <EnrichmentCard />

    <AddSourceModal
      v-if="showAddSourceModal"
      @close="showAddSourceModal = false"
      @created="() => (showAddSourceModal = false)"
    />
  </div>
</template>

<style scoped>
.sync-sources-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.sync-accordion-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.sync-all-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 2px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.18);
}
</style>
