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
  if (data.isSourceIdSyncing('all')) return 'Syncing...'
  return 'Sync All Sources'
})

// A source whose plugin never loaded has no schema and no config to fetch, so
// it gets its own notice instead of an accordion that would 404 on expand.
const unusableSources = computed(() =>
  data.syncSources.filter((source) => source.plugin_not_loaded !== null),
)

// Enabled sources first, disabled sources collapsed at the bottom in a
// muted state. Within each group preserve the API ordering (already
// alphabetical by source id).
const orderedSources = computed(() => {
  return data.syncSources
    .filter((source) => source.plugin_not_loaded === null)
    .sort((a, b) => {
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
      <p class="help-text">
        Sync data from your configured sources. Multiple sources can run in
        parallel.
      </p>

      <div v-if="data.syncLoading" class="empty-state"><span class="spinner" /> Loading sync sources...</div>
      <div v-else-if="data.syncSourcesError" class="empty-state">
        <!-- The Retry button sits OUTSIDE the alert: alert content is announced
             as one chunk, which buries the control's affordance. -->
        <span role="alert">Couldn't load sync sources.</span>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="sync-sources-retry"
          @click="data.loadSyncSources()"
        >Retry</button>
      </div>
      <template v-else>
        <!--
          Plain content, not a live region: it is already populated on first
          render, and a region that arrives that way is read as page content
          rather than a status change (WCAG 4.1.3).
        -->
        <div
          v-if="unusableSources.length"
          class="unusable-sources"
          data-testid="unusable-sources"
        >
          <p class="unusable-sources-title">
            These sources cannot run until their plugin loads:
          </p>
          <ul class="unusable-sources-list">
            <li v-for="source in unusableSources" :key="source.id">
              {{ source.display_name }}: plugin
              "{{ source.plugin_not_loaded?.plugin }}" is not loaded. These
              modules failed to import:
              <ul>
                <li
                  v-for="failure in source.plugin_not_loaded?.failures"
                  :key="failure.module"
                >{{ failure.module }}: {{ failure.reason }}</li>
              </ul>
            </li>
          </ul>
        </div>
        <div v-if="orderedSources.length" class="sync-accordion-list">
          <SyncSourceAccordion
            v-for="source in orderedSources"
            :key="source.id"
            :source="source"
            :syncing="data.isSourceIdSyncing(source.id)"
            :job="data.jobForSourceId(source.id)"
            @sync="data.triggerSync($event)"
          />
          <div v-if="orderedSources.length > 1" class="sync-all-card">
            <div>
              <h3>All Sources</h3>
              <p class="sync-plugin-name">Sync all enabled sources at once</p>
            </div>
            <button
              type="button"
              class="btn btn-secondary sync-btn"
              :disabled="data.isSourceIdSyncing('all')"
              :aria-label="
                data.isSourceIdSyncing('all')
                  ? 'Syncing all sources — in progress'
                  : 'Sync all sources'
              "
              @click="data.triggerSync('all')"
            >{{ syncAllLabel }}</button>
          </div>
        </div>
        <div v-else-if="unusableSources.length === 0" class="empty-state">
          No sync sources configured. Add sources to config.yaml with enabled: true.
        </div>
      </template>
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

.unusable-sources {
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-lg);
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 18%, transparent);
}

.unusable-sources-title {
  margin: 0 0 var(--space-1);
  font-weight: 600;
}

.unusable-sources-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.unusable-sources-list li + li {
  margin-top: var(--space-1);
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
