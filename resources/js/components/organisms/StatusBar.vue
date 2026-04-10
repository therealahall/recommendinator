<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()
</script>

<template>
  <div
    v-show="app.statusMessage"
    class="status-bar"
    :role="app.status === 'error' ? 'alert' : 'status'"
    :aria-live="app.status === 'error' ? 'assertive' : 'polite'"
    aria-atomic="true"
    :class="{
      error: app.status === 'error',
      loading: app.status === 'loading',
    }"
  >
    {{ app.statusMessage }}
  </div>
</template>
