<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()

function reload() {
  location.reload()
}
</script>

<template>
  <div v-if="app.showUpdateBanner" class="update-banner visible">
    A new version is available.
    <button class="update-banner-btn" @click="reload">Reload</button>
  </div>
</template>
