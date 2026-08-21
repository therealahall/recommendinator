<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import DuplicateHistory from '@/components/organisms/DuplicateHistory.vue'
import DuplicateQueue from '@/components/organisms/DuplicateQueue.vue'
import { useDuplicatesStore } from '@/stores/duplicates'

const store = useDuplicatesStore()
const alertEl = ref<HTMLElement | null>(null)

// A refused block sits screens below the one region that explains it.
watch(
  () => store.error,
  (message) => {
    if (message) alertEl.value?.scrollIntoView({ block: 'center' })
  },
  { flush: 'post' },
)

onMounted(() => {
  store.loadAll()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Duplicates</h2>
      <p class="page-description">
        The same work held more than once, usually from more than one source.
        Merge a work’s copies keeping the one you want, or dismiss a copy that
        is a different work.
      </p>
    </div>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p ref="alertEl" class="dup-alert" role="alert">{{ store.error }}</p>
    <p class="sr-only" role="status" aria-live="polite">{{ store.announcement }}</p>

    <DuplicateQueue />
    <DuplicateHistory />
  </div>
</template>

<style scoped>
.dup-alert {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-error-text);
}

.dup-alert:not(:empty) {
  margin-bottom: var(--space-4);
}
</style>
