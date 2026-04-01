<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const visible = ref(true)
let hideTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => app.status,
  (status) => {
    visible.value = true
    if (hideTimer) clearTimeout(hideTimer)
    if (status === 'ready') {
      hideTimer = setTimeout(() => {
        visible.value = false
      }, 3000)
    }
  },
)

onUnmounted(() => {
  if (hideTimer) clearTimeout(hideTimer)
})
</script>

<template>
  <div
    v-show="visible && app.statusMessage"
    class="status-bar"
    :role="app.status === 'error' ? 'alert' : 'status'"
    :aria-live="app.status === 'error' ? 'assertive' : 'polite'"
    aria-atomic="true"
    :class="{
      success: app.status === 'ready',
      error: app.status === 'error',
      loading: app.status === 'loading',
    }"
  >
    {{ app.statusMessage }}
  </div>
</template>
