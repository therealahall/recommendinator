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
        Merge a work’s copies keeping the one you want, or dismiss a copy that
        is a different work.
      </p>
    </div>

    <!-- Mounted while silent: inserted already populated it reads as page
         content, not a change (WCAG 4.1.3). sr-only only where a history row
         prints the words; a queue refusal shows here, its block re-keyed. -->
    <p
      class="dup-alert"
      :class="{ 'sr-only': store.errorKey !== '' }"
      role="alert"
    >{{ store.error }}</p>
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
