<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const retrying = ref(false)

const message = computed(() => (retrying.value ? 'Reconnecting…' : app.statusMessage))

async function retry(): Promise<void> {
  if (retrying.value) return
  retrying.value = true
  await app.fetchStatus()
  retrying.value = false
}
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
    {{ message }}
    <button
      v-if="app.status === 'error'"
      type="button"
      class="btn btn-secondary btn-small"
      data-testid="status-retry"
      :aria-disabled="retrying || undefined"
      @click="retry"
    >
      Try again
    </button>
  </div>
</template>
