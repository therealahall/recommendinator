<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { rescueFocus } from '@/utils/focus'
import AppIcon from '@/components/atoms/AppIcon.vue'
import SyncSourceAccordion from '@/components/organisms/SyncSourceAccordion.vue'
import AddSourceModal from '@/components/organisms/AddSourceModal.vue'
import EnrichmentCard from '@/components/organisms/EnrichmentCard.vue'
import ImportPanel from '@/components/organisms/ImportPanel.vue'

const data = useDataStore()
const showAddSourceModal = ref(false)
const retryingSources = ref(false)
const retryMessage = ref('')
const sourcesPanel = ref<HTMLElement | null>(null)

async function onRetrySources(): Promise<void> {
  if (retryingSources.value) return
  const focused = document.activeElement
  retryingSources.value = true
  // The in-flight line is what makes a second failure audible: setting the same
  // words twice leaves the region's text unchanged, so it announces nothing.
  retryMessage.value = 'Reloading sync sources…'
  await data.loadSyncSources()
  retryingSources.value = false
  retryMessage.value = data.syncSourcesError
    ? "Still couldn't load sync sources. Try again in a moment."
    : 'Sync sources loaded.'
  await nextTick()
  // Success unmounts the failure branch, Retry included (WCAG 2.4.3). Only from
  // <body>: someone who Tabbed away mid-request must not be yanked back.
  if (
    focused instanceof HTMLElement &&
    !focused.isConnected &&
    (document.activeElement === null || document.activeElement === document.body)
  ) {
    sourcesPanel.value?.focus()
  }
}

async function onSourceCreated(): Promise<void> {
  showAddSourceModal.value = false
  await nextTick()
  rescueFocus(sourcesPanel.value)
}

function onSyncAll(): void {
  if (data.anySyncRunning) return
  data.triggerSync('all')
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

// A source whose plugin never loaded has no schema and no config to fetch, so
// it gets its own notice instead of an accordion that would 404 on expand.
const unusableSources = computed(() =>
  data.syncSources.filter((source) => source.plugin_not_loaded !== null),
)

// Within each group preserve the API ordering (already alphabetical by source id).
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
      <p class="page-description">
        Import a file, sync sources, and enrich metadata from external APIs.
      </p>
    </div>

    <ImportPanel />

    <!-- Named and focusable, because a successful retry sends focus here. -->
    <div
      ref="sourcesPanel"
      class="card focus-fallback"
      data-testid="sync-sources-panel"
      role="group"
      aria-labelledby="sync-sources-heading"
      tabindex="-1"
    >
      <div class="sync-sources-header">
        <h3 id="sync-sources-heading" class="section-title">Sync sources</h3>
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

      <div
        v-if="data.syncLoading && !retryingSources"
        class="state state--loading"
      ><span class="spinner" /> Loading sync sources…</div>
      <!-- The failure branch outlives the retry it started: replacing it would
           unmount the button holding focus and drop the user to <body>
           (WCAG 2.4.3). -->
      <div v-else-if="data.syncSourcesError || retryingSources" class="state state--error">
        <!-- The Retry button sits OUTSIDE the alert: alert content is announced
             as one chunk, which buries the control's affordance. -->
        <span role="alert">Couldn't load sync sources.</span>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="sync-sources-retry"
          :aria-disabled="retryingSources || undefined"
          @click="onRetrySources"
        >{{ retryingSources ? 'Retrying…' : 'Retry' }}</button>
      </div>
      <template v-else>
        <!-- Plain content, not a live region: populated on first render, it
             would be read as content rather than announced (WCAG 4.1.3). -->
        <div
          v-if="unusableSources.length"
          class="state state--error unusable-sources"
          data-testid="unusable-sources"
        >
          <p class="unusable-sources-title">
            These sources cannot run until their plugin loads:
          </p>
          <ul class="unusable-sources-list" role="list">
            <li v-for="source in unusableSources" :key="source.id">
              {{ source.display_name }}: plugin
              "{{ source.plugin_not_loaded?.plugin }}" is not loaded. These
              modules failed to import:
              <ul role="list">
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
              <h3 class="section-subtitle">All sources</h3>
              <p class="sync-plugin-name">Sync every enabled source at once</p>
            </div>
            <button
              type="button"
              class="btn btn-secondary sync-btn"
              :aria-disabled="data.anySyncRunning || undefined"
              :aria-label="
                data.isSourceIdSyncing('all')
                  ? 'Syncing all sources — in progress'
                  : data.anySyncRunning
                  ? 'Sync all sources — another sync is in progress'
                  : 'Sync all sources'
              "
              @click="onSyncAll"
            >{{ syncAllLabel }}</button>
          </div>
        </div>
        <div v-else-if="unusableSources.length === 0" class="state state--empty">
          <span class="state-mark"><AppIcon name="activity" :size="20" /></span>
          <p class="state-title">No sources configured</p>
          <p class="state-hint">
            A source is an account or a file feed this instance pulls from on a
            cadence. Add one and its runs, its schedule and its last outcome
            appear here.
          </p>
          <div class="state-actions">
            <button
              type="button"
              class="btn btn-primary"
              data-testid="add-first-source-btn"
              @click="showAddSourceModal = true"
            >Add a source</button>
          </div>
        </div>
      </template>

      <!-- Outside the branches above, so a retry finds it already in the
           accessibility tree (WCAG 4.1.3). -->
      <p
        class="retry-status"
        data-testid="sync-sources-retry-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >{{ retryMessage }}</p>
    </div>

    <EnrichmentCard />

    <AddSourceModal
      v-if="showAddSourceModal"
      @close="showAddSourceModal = false"
      @created="onSourceCreated"
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
  display: block;
  margin-bottom: var(--space-3);
}

.unusable-sources-title {
  margin: 0 0 var(--space-1);
  font-weight: var(--weight-semibold);
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
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  box-shadow: var(--elevation-1);
}

</style>
