<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()

function reload() {
  location.reload()
}
</script>

<template>
  <div v-if="app.showUpdateBanner" class="update-banner visible" role="alert" aria-live="assertive">
    <!-- One flex item, not five: the banner row does not wrap, and each <code>
         would otherwise set its own min-content floor. -->
    <span v-if="app.staleBundle">
      This page was built from {{ app.loadedVersion }} and the server runs
      {{ app.version }}. Reloading serves the same files again: rebuild on the host
      with <code>pnpm build</code>, or under Docker run
      <code>docker compose up -d --build --renew-anon-volumes</code>.
    </span>
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
