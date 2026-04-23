<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useDataStore } from '@/stores/data'
import SyncSourceCard from '@/components/molecules/SyncSourceCard.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import EnrichmentCard from '@/components/organisms/EnrichmentCard.vue'

const data = useDataStore()

onMounted(() => {
  data.loadSyncSources()
  data.checkSyncStatus()
  data.loadEnrichmentStats()
  data.checkEnrichmentStatus()
})

onUnmounted(() => {
  data.cleanup()
})

const gogState = computed(() => ({
  needsConnect: !data.gogStatus.connected && !!data.gogStatus.authUrl,
  showDisconnect: data.gogStatus.connected,
}))

const epicState = computed(() => ({
  needsConnect: !data.epicStatus.connected && !!data.epicStatus.authUrl,
  showDisconnect: data.epicStatus.connected,
}))

const syncAllLabel = computed(() => {
  if (data.syncStatus === 'running' && data.syncingSource === 'all') return 'Syncing...'
  if (data.syncStatus === 'running') return 'Sync in Progress'
  return 'Sync All Sources'
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Data</h2>
      <p class="page-description">Sync sources and enrich metadata from external APIs.</p>
    </div>

    <div class="card">
      <h2>Sync Sources</h2>
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
      <div v-else class="sync-grid">
        <SyncSourceCard
          v-for="source in data.syncSources"
          :key="source.id"
          :source="source"
          :syncing="data.syncingSource === source.id || data.syncingSource === 'all'"
          :disabled="data.syncStatus === 'running' && data.syncingSource !== source.id && data.syncingSource !== 'all'"
          :show-sync-button="
            !(source.id === 'gog' && gogState.needsConnect) &&
            !(source.id === 'epic_games' && epicState.needsConnect)
          "
          @sync="data.triggerSync($event)"
        >
          <template v-if="source.id === 'gog' && gogState.needsConnect">
            <OAuthConnectFlow
              :auth-url="data.gogStatus.authUrl"
              expected-origin="https://login.gog.com"
              :connect-message="data.gogConnectMessage"
              help-text="Paste the redirect URL after logging in:"
              service-name="GOG Account"
              @submit="data.submitGogCode($event)"
            />
          </template>
          <template v-else-if="source.id === 'epic_games' && epicState.needsConnect">
            <OAuthConnectFlow
              :auth-url="data.epicStatus.authUrl"
              expected-origin="https://www.epicgames.com"
              :connect-message="data.epicConnectMessage"
              help-text="Paste the authorization code from the JSON response:"
              service-name="Epic Games"
              @submit="data.submitEpicCode($event)"
            />
          </template>
          <template v-else-if="source.id === 'gog' && gogState.showDisconnect">
            <p
              v-if="data.gogConnectMessage"
              class="sr-only"
              aria-live="polite"
            >{{ data.gogConnectMessage }}</p>
            <button
              type="button"
              class="btn btn-danger disconnect-btn"
              :disabled="data.syncStatus === 'running'"
              aria-label="Disconnect GOG"
              @click="data.disconnectGog()"
            >Disconnect</button>
          </template>
          <template v-else-if="source.id === 'epic_games' && epicState.showDisconnect">
            <p
              v-if="data.epicConnectMessage"
              class="sr-only"
              aria-live="polite"
            >{{ data.epicConnectMessage }}</p>
            <button
              type="button"
              class="btn btn-danger disconnect-btn"
              :disabled="data.syncStatus === 'running'"
              aria-label="Disconnect Epic Games"
              @click="data.disconnectEpic()"
            >Disconnect</button>
          </template>
        </SyncSourceCard>
        <div v-if="data.syncSources.length > 1" class="sync-card">
          <h3>All Sources</h3>
          <p class="sync-plugin-name">Sync all enabled sources at once</p>
          <button
            class="btn btn-secondary sync-btn"
            :disabled="data.syncStatus === 'running'"
            @click="data.triggerSync('all')"
          >{{ syncAllLabel }}</button>
        </div>
      </div>
    </div>

    <EnrichmentCard />
  </div>
</template>

<style scoped>
/* .btn is display:inline-flex, so the Sync button would otherwise sit next
   to the Disconnect button on the same line. Force a block break so they
   stack with spacing — separating the destructive action from Sync also
   makes misclicks less likely. */
.disconnect-btn {
  display: flex;
  margin: 0 auto var(--space-3);
}
</style>
