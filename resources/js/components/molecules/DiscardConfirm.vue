<script setup lang="ts">
import { onMounted, ref } from 'vue'

defineEmits<{
  discard: []
  keep: []
}>()

const panel = ref<HTMLElement | null>(null)

// Focused where it renders, so the group's name reads the question. Not
// alertdialog: it inerts nothing, so Tab walks straight out into the form.
onMounted(() => panel.value?.focus())
</script>

<template>
  <div
    ref="panel"
    class="discard-confirm"
    role="group"
    aria-labelledby="discard-confirm-message"
    tabindex="-1"
  >
    <p id="discard-confirm-message">Discard your unsaved changes?</p>
    <div class="discard-confirm-actions">
      <button class="btn btn-secondary" @click="$emit('keep')">Keep editing</button>
      <button class="btn btn-primary" @click="$emit('discard')">Discard</button>
    </div>
  </div>
</template>

<style scoped>
.discard-confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-primary);
}

.discard-confirm p {
  margin: 0;
}

.discard-confirm-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
