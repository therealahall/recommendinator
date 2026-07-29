<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import SyncSourceAccordion from '@/components/organisms/SyncSourceAccordion.vue'
import AddSourceModal from '@/components/organisms/AddSourceModal.vue'
import ImportFileModal from '@/components/organisms/ImportFileModal.vue'
import EnrichmentCard from '@/components/organisms/EnrichmentCard.vue'
import type { SyncSourceResponse } from '@/types/api'

const data = useDataStore()
const showAddSourceModal = ref(false)
const showImportModal = ref(false)
const addSourceButton = ref<HTMLButtonElement | null>(null)
const removedMessage = ref('')

// Removing a source unmounts its accordion, taking the focused Remove button
// with it — focus would fall to <body> and a screen reader would hear nothing
// at all. Say what happened and put the caret on the nearest stable control
// above the list (WCAG 2.4.3 / 4.1.3).
//
// Focus lands FIRST, then the live region is mutated a tick later: several
// screen readers drop a queued polite announcement when a focus change arrives
// alongside it, and this announcement is the only signal a non-sighted user
// gets — the sighted confirmation is the row disappearing.
async function onSourceRemoved(displayName: string): Promise<void> {
  await nextTick()
  addSourceButton.value?.focus()
  await nextTick()
  removedMessage.value = `Removed ${displayName} from the database.`
}

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

// Enabled sources first, then disabled ones, then leftover file-import
// entries — which can never sync whatever their enabled flag says, so they
// belong below both. Within each group preserve the API ordering (already
// alphabetical by source id).
function sortRank(source: SyncSourceResponse): number {
  if (source.is_file_import) return 2
  return source.enabled ? 0 : 1
}

const orderedSources = computed(() =>
  [...data.syncSources].sort((a, b) => sortRank(a) - sortRank(b)),
)
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
        <div class="sync-sources-header-actions">
          <button
            ref="addSourceButton"
            type="button"
            class="btn btn-primary"
            data-testid="add-source-btn"
            aria-haspopup="dialog"
            @click="showAddSourceModal = true"
          >+ Add source</button>
          <button
            type="button"
            class="btn btn-secondary"
            data-testid="import-file-btn"
            aria-haspopup="dialog"
            @click="showImportModal = true"
          >Import from file</button>
        </div>
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
      <!--
        Permanently mounted so a removal is announced when the text arrives —
        some screen readers skip a live region that is inserted with content
        already in it. Nothing visible: the row vanishing is the sighted
        confirmation.
      -->
      <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {{ removedMessage }}
      </p>

      <div v-if="data.syncLoading" class="empty-state"><span class="spinner" /> Loading sync sources...</div>
      <div v-else-if="data.syncSources.length === 0" class="empty-state">
        No sync sources configured. Add sources to config.yaml with enabled: true.
      </div>
      <div v-else class="sync-accordion-list">
        <SyncSourceAccordion
          v-for="source in orderedSources"
          :key="source.id"
          :source="source"
          :syncing="data.isSourceIdSyncing(source.id) || data.isSourceIdSyncing('all')"
          :job="data.jobForSourceId(source.id) || data.jobForSourceId('all')"
          @sync="data.triggerSync($event)"
          @removed="onSourceRemoved"
        />
        <div v-if="data.syncSources.length > 1" class="sync-all-card">
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
    </div>

    <EnrichmentCard />

    <AddSourceModal
      v-if="showAddSourceModal"
      @close="showAddSourceModal = false"
      @created="() => (showAddSourceModal = false)"
    />

    <ImportFileModal
      v-if="showImportModal"
      @close="showImportModal = false"
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

.sync-sources-header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
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
