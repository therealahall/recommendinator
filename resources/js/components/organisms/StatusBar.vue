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
    <button
      v-if="app.status === 'error'"
      type="button"
      class="btn btn-secondary btn-small"
      data-testid="status-retry"
      @click="app.fetchStatus()"
    >
      Try again
    </button>
  </div>
</template>
