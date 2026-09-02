<script setup lang="ts">
import { ref } from 'vue'
import { useFocusTrap } from '@/composables/useFocusTrap'

withDefaults(
  defineProps<{
    labelledBy: string
    wide?: boolean
  }>(),
  { wide: false },
)

const emit = defineEmits<{ dismiss: [] }>()

const surface = ref<HTMLElement | null>(null)

useFocusTrap(surface, () => emit('dismiss'))

defineExpose({ surface })
</script>

<template>
  <div class="dialog-backdrop" @click.self="emit('dismiss')">
    <div
      ref="surface"
      class="dialog-surface"
      :class="{ 'dialog-surface-wide': wide }"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="labelledBy"
      tabindex="-1"
    >
      <slot />
      <div v-if="$slots.actions" class="dialog-actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-3);
  background: var(--overlay-dark);
  backdrop-filter: blur(4px);
}

.dialog-surface {
  width: 100%;
  max-width: 500px;
  max-height: 100%;
  overflow-y: auto;
  padding: var(--space-5);
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-3);
}

.dialog-surface-wide {
  max-width: 38rem;
}

.dialog-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-3);
  margin-top: var(--space-5);
}
</style>
