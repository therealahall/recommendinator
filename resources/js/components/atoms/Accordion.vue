<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** Stable id used to derive trigger/panel ids — must be unique per page. */
    id: string
    expanded: boolean
    /** Heading level for the trigger, so it nests correctly under the
     *  surrounding section heading (defaults to h3). */
    headingLevel?: 2 | 3 | 4 | 5 | 6
  }>(),
  { headingLevel: 3 },
)

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
      <component :is="`h${headingLevel}`" class="accordion-heading">
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
      </component>
      <div v-if="$slots['header-actions']" class="accordion-header-actions">
        <slot name="header-actions" />
      </div>
    </div>
    <!-- Outside the trigger button and outside the collapsible panel: what
         goes here has to be readable without expanding, and must not be
         swallowed into the trigger's accessible name. -->
    <slot name="notice" />
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
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  box-shadow: var(--elevation-1);
  transition: box-shadow var(--transition-fast), border-color var(--transition-fast);
}

.accordion:hover {
  border-color: var(--accent);
  box-shadow: var(--elevation-2);
}

.accordion--expanded {
  border-color: var(--accent);
  box-shadow: var(--elevation-2);
}

.accordion-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  transition: background var(--transition-fast);
}

.accordion-row:hover,
.accordion-row:focus-within {
  background: var(--bg-hover);
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

.accordion-chevron {
  transition: transform var(--transition-base);
  flex-shrink: 0;
}

.accordion--expanded .accordion-chevron {
  transform: rotate(180deg);
}

.accordion-panel-inner {
  padding: var(--space-3) var(--space-4) var(--space-4);
  border-top: 1px solid var(--border-default);
}
</style>
