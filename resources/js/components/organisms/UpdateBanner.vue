<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()

function reload() {
  location.reload()
}
</script>

<template>
  <div v-if="app.showUpdateBanner" class="update-banner visible" role="alert" aria-live="assertive">
    A new version is available.
    <button class="btn btn-secondary btn-small" @click="reload">Reload</button>
  </div>
  <div v-if="app.dependencyDrift.length" class="update-banner visible" role="status">
    Dependency drift: {{ app.dependencyDrift.map((entry) => entry.message).join('; ') }}
  </div>
</template>
