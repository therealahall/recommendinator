<script setup lang="ts">
import type { SyncSourceResponse } from '@/types/api'

const props = withDefaults(defineProps<{
  source: SyncSourceResponse
  syncing: boolean
  showSyncButton?: boolean
}>(), {
  showSyncButton: true,
})

const emit = defineEmits<{
  sync: [sourceId: string]
}>()
</script>

<template>
  <div class="sync-card">
    <h3>{{ source.display_name }}</h3>
    <p class="sync-plugin-name">Plugin: {{ source.plugin_display_name }}</p>
    <slot />
    <button
      v-if="showSyncButton"
      class="btn btn-primary sync-btn"
      :disabled="syncing"
      @click="emit('sync', source.id)"
    >{{ syncing ? 'Syncing...' : 'Sync' }}</button>
  </div>
</template>
