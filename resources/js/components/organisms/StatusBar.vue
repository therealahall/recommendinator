<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const retrying = ref(false)

const message = computed(() => (retrying.value ? 'Reconnecting…' : app.statusMessage))

async function retry(): Promise<void> {
  if (retrying.value) return
  const trigger = document.activeElement
  retrying.value = true
  await app.fetchStatus()
  retrying.value = false
  await nextTick()
  const triggerUnmounted = trigger instanceof HTMLElement && !trigger.isConnected
  const focusFellToBody =
    document.activeElement === null || document.activeElement === document.body
  if (triggerUnmounted && focusFellToBody) {
    document.getElementById('main-content')?.focus()
  }
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
