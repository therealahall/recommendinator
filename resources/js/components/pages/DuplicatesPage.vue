<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '@/components/atoms/AppIcon.vue'
import DuplicateHistory from '@/components/organisms/DuplicateHistory.vue'
import DuplicateQueue from '@/components/organisms/DuplicateQueue.vue'
import { REFUSAL_ALERT_ID, useDuplicatesStore } from '@/stores/duplicates'

const store = useDuplicatesStore()

onMounted(() => {
  store.loadAll()
})
</script>

<template>
  <div>
    <div class="page-header">
      <!-- The rail marks Library while this is on screen, so the way back to it
           has to be on the page itself. -->
      <RouterLink class="dup-back" :to="{ name: 'library' }">
        <AppIcon name="book" />
        Library
      </RouterLink>
      <h2>Duplicates</h2>
      <p class="page-description">
        The same work held more than once, usually from more than one source.
        Merge a work’s copies keeping the one you want, or dismiss a copy that
        is a different work.
      </p>
    </div>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p
      :id="REFUSAL_ALERT_ID"
      class="state state--error dup-alert focus-fallback"
      role="alert"
      tabindex="-1"
    >{{ store.error }}</p>
    <p class="sr-only" role="status" aria-live="polite">{{ store.announcement }}</p>

    <DuplicateQueue />
    <DuplicateHistory />
  </div>
</template>

<style scoped>
.dup-alert:not(:empty) {
  margin-bottom: var(--space-4);
}

.dup-back {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  min-height: 44px;
  margin-bottom: var(--space-1);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-widest);
  text-transform: uppercase;
  color: var(--text-muted);
  text-decoration: none;
}

.dup-back:hover {
  color: var(--text-primary);
  text-decoration: underline;
}
</style>
