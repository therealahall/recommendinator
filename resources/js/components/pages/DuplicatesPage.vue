<script setup lang="ts">
import { onMounted } from 'vue'
import DuplicateHistory from '@/components/organisms/DuplicateHistory.vue'
import DuplicateQueue from '@/components/organisms/DuplicateQueue.vue'
import { useDuplicatesStore } from '@/stores/duplicates'

const store = useDuplicatesStore()

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
        Merge a pair keeping the row you want, or say it is not a pair.
      </p>
    </div>

    <!-- Both regions stay mounted while silent: one inserted already populated
         is read as page content rather than a change (WCAG 4.1.3). -->
    <p class="dup-alert" role="alert">{{ store.error }}</p>
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
