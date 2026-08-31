<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { rescueFocus } from '@/utils/focus'

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
// Several panels can be open at once, and a duplicated id names only the first.
const messageId = useId()

let opener: HTMLElement | null = null

// Focused where it renders, so the group's name reads the question. Not
// alertdialog: it inerts nothing, so Tab walks straight out into the form.
onMounted(() => {
  const active = document.activeElement
  opener = active instanceof HTMLElement && active !== document.body ? active : null
  panel.value?.focus()
})

// Answering unmounts the panel under the focus it took, so the keyboard goes
// back to what opened it (WCAG 2.4.3) — every caller needed this, and two of
// four had it.
onBeforeUnmount(() => {
  const back = opener
  // Not back to an opener the answer took away with it.
  void nextTick(() => rescueFocus(back?.isConnected ? back : null))
})
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
      <!-- Described by the question: Tab landing straight on a button reads
           only its label, and two open panels both offer "Keep it". -->
      <button
        type="button"
        class="btn btn-secondary"
        data-testid="confirm-panel-cancel"
        :aria-describedby="messageId"
        @click="$emit('cancel')"
      >{{ cancelLabel }}</button>
      <button
        type="button"
        class="btn"
        :class="destructive ? 'btn-danger' : 'btn-primary'"
        data-testid="confirm-panel-confirm"
        :aria-describedby="messageId"
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
