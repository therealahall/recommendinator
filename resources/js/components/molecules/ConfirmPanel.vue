<script setup lang="ts">
import { onMounted, ref, useId } from 'vue'

withDefaults(
  defineProps<{
    message: string
    confirmLabel: string
    cancelLabel: string
    destructive?: boolean
  }>(),
  { destructive: false },
)

defineEmits<{
  confirm: []
  cancel: []
}>()

const panel = ref<HTMLElement | null>(null)
// Several panels can be open at once — one per source accordion — and a
// duplicated id resolves to the first match, unnaming every later group.
const messageId = useId()

// Focused where it renders, so the group's name reads the question. Not
// alertdialog: it inerts nothing, so Tab walks straight out into the form.
onMounted(() => panel.value?.focus())
</script>

<template>
  <div
    ref="panel"
    class="confirm-panel"
    data-testid="confirm-panel"
    role="group"
    :aria-labelledby="messageId"
    tabindex="-1"
  >
    <p :id="messageId">{{ message }}</p>
    <div class="confirm-panel-actions">
      <button
        type="button"
        class="btn btn-secondary"
        data-testid="confirm-panel-cancel"
        @click="$emit('cancel')"
      >{{ cancelLabel }}</button>
      <button
        type="button"
        class="btn"
        :class="destructive ? 'btn-danger' : 'btn-primary'"
        data-testid="confirm-panel-confirm"
        @click="$emit('confirm')"
      >{{ confirmLabel }}</button>
    </div>
  </div>
</template>

<style scoped>
.confirm-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-primary);
}

.confirm-panel p {
  margin: 0;
}

.confirm-panel-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
