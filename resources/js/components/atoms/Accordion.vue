<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  /** Stable id used to derive trigger/panel ids — must be unique per page. */
  id: string
  expanded: boolean
}>()

const emit = defineEmits<{
  'update:expanded': [value: boolean]
}>()

const triggerId = computed(() => `accordion-${props.id}-trigger`)
const panelId = computed(() => `accordion-${props.id}-panel`)

function toggle(): void {
  emit('update:expanded', !props.expanded)
}
</script>

<template>
  <div class="accordion" :class="{ 'accordion--expanded': expanded }">
    <div class="accordion-row">
      <h3 class="accordion-heading">
        <button
          :id="triggerId"
          type="button"
          class="accordion-trigger"
          :aria-expanded="expanded"
          :aria-controls="panelId"
          @click="toggle"
        >
          <slot name="header" />
          <span class="accordion-chevron" aria-hidden="true">▾</span>
        </button>
      </h3>
      <div v-if="$slots['header-actions']" class="accordion-header-actions">
        <slot name="header-actions" />
      </div>
    </div>
    <div
      :id="panelId"
      role="region"
      class="accordion-panel"
      :aria-labelledby="triggerId"
      :hidden="!expanded"
    >
      <div class="accordion-panel-inner">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.accordion {
  border: 2px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.18);
  transition: box-shadow 0.15s ease, border-color 0.15s ease;
}

.accordion:hover {
  border-color: var(--accent);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.25);
}

.accordion--expanded {
  border-color: var(--accent);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3);
}

@media (prefers-reduced-motion: reduce) {
  .accordion {
    transition: none;
  }
}

.accordion-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  transition: background 0.15s ease;
}

.accordion-row:hover,
.accordion-row:focus-within {
  background: var(--bg-hover, rgba(255, 255, 255, 0.04));
}

@media (prefers-reduced-motion: reduce) {
  .accordion-row {
    transition: none;
  }
}

.accordion-heading {
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  flex: 1;
}

.accordion-header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-right: var(--space-4);
}

.accordion-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
  color: var(--text-primary);
  font: inherit;
  gap: var(--space-3);
}

.accordion-trigger:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.accordion-chevron {
  transition: transform 0.2s ease;
  flex-shrink: 0;
}

.accordion--expanded .accordion-chevron {
  transform: rotate(180deg);
}

.accordion-panel-inner {
  padding: var(--space-3) var(--space-4) var(--space-4);
  border-top: 1px solid var(--border-default);
}

@media (prefers-reduced-motion: reduce) {
  .accordion-chevron {
    transition: none;
  }
}
</style>
