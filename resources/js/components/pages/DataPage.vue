<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
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

function needsGogConnect(sourceId: string): boolean {
  return sourceId === 'gog' && !data.gogStatus.connected && !!data.gogStatus.authUrl
}

function needsEpicConnect(sourceId: string): boolean {
  return sourceId === 'epic_games' && !data.epicStatus.connected && !!data.epicStatus.authUrl
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Data</h2>
      <p class="page-description">Sync sources and enrich metadata from external APIs.</p>
    </div>

    <div class="card">
      <h2>Sync Sources</h2>
      <div v-if="data.syncMessage" class="sync-status-message" :class="{
        'sync-status-error': data.syncStatus === 'failed',
        'sync-status-success': data.syncStatus === 'completed',
        'sync-status-info': data.syncStatus === 'running' || data.syncStatus === 'idle',
      }">{{ data.syncMessage }}</div>
      <div v-if="data.syncJob?.progress_percent && data.syncStatus === 'running'" class="sync-progress-bar">
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
          :syncing="data.syncStatus === 'running'"
          @sync="data.triggerSync($event)"
        >
          <OAuthConnectFlow
            v-if="needsGogConnect(source.id)"
            :auth-url="data.gogStatus.authUrl"
            expected-origin="https://login.gog.com"
            :connect-message="data.gogConnectMessage"
            help-text="Paste the redirect URL after logging in:"
            service-name="GOG Account"
            @submit="data.submitGogCode($event)"
          />
          <OAuthConnectFlow
            v-else-if="needsEpicConnect(source.id)"
            :auth-url="data.epicStatus.authUrl"
            expected-origin="https://www.epicgames.com"
            :connect-message="data.epicConnectMessage"
            help-text="Paste the authorization code from the JSON response:"
            service-name="Epic Games"
            @submit="data.submitEpicCode($event)"
          />
        </SyncSourceCard>
        <div v-if="data.syncSources.length > 1" class="sync-card">
          <h3>All Sources</h3>
          <p class="sync-plugin-name">Sync all enabled sources at once</p>
          <button
            class="btn btn-secondary sync-btn"
            :disabled="data.syncStatus === 'running'"
            @click="data.triggerSync('all')"
          >{{ data.syncStatus === 'running' ? 'Syncing...' : 'Sync All Sources' }}</button>
        </div>
      </div>
    </div>

    <EnrichmentCard />
  </div>
</template>
