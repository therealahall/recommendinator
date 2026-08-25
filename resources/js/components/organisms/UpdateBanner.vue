<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()

function reload() {
  location.reload()
}
</script>

<template>
  <div v-if="app.showUpdateBanner" class="update-banner visible" role="alert" aria-live="assertive">
    <template v-if="app.staleBundle">
      This page was built from {{ app.loadedVersion }} and the server runs
      {{ app.version }}. Rebuild the frontend (pnpm build) to catch up — reloading
      serves the same files again.
    </template>
    <template v-else>
      A new version is available.
      <button class="btn btn-secondary btn-small" @click="reload">Reload</button>
    </template>
  </div>
  <div role="status">
    <div
      v-if="app.dependencyDrift.length"
      class="update-banner update-banner--drift visible"
    >
      Dependency drift:
      <ul>
        <li v-for="entry in app.dependencyDrift" :key="entry.package">
          {{ entry.message }}
        </li>
      </ul>
    </div>
  </div>
</template>
