<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'
import ScorerSlider from '@/components/atoms/ScorerSlider.vue'
import { useFocusTrap } from '@/composables/useFocusTrap'
import {
  SCORER_KEYS,
  SCORER_TOOLTIPS,
  VARIETY_PENALTY_TOOLTIP,
  usePreferencesStore,
} from '@/stores/preferences'
import { formatScorerName } from '@/utils/format'

const emit = defineEmits<{ close: [] }>()

const prefs = usePreferencesStore()
const panel = ref<HTMLElement | null>(null)

useFocusTrap(panel, dismiss)

/** What Revert goes back to: this visit's opening values, or the last save. */
const opened = ref<{ weights: Record<string, number>; variety: number } | null>(null)

const status = computed(() => {
  if (prefs.saveStatus === 'error') return prefs.saveError
  if (prefs.saveStatus === 'saving') return 'Saving…'
  if (prefs.saveStatus === 'saved') return 'Saved. Recommend again to use them.'
  return ''
})

function take() {
  opened.value = { weights: { ...prefs.scorerWeights }, variety: prefs.varietyPenalty }
}

function revert() {
  if (!opened.value) return
  prefs.scorerWeights = { ...opened.value.weights }
  prefs.varietyPenalty = opened.value.variety
}

function dismiss() {
  emit('close')
}

async function save() {
  await prefs.save()
  if (prefs.saveStatus !== 'error') take()
}

onMounted(async () => {
  if (!prefs.hasLoaded) await prefs.load()
  take()
})

/** The sliders write straight to the shared store, and every way out — the
 *  close button, Escape, the scrim, a route change — ends here. */
onUnmounted(revert)
</script>

<template>
  <div class="weights-scrim" @click="dismiss" />
  <aside
    id="weights-panel"
    ref="panel"
    class="weights-panel"
    role="dialog"
    aria-modal="true"
    aria-labelledby="weights-heading"
    tabindex="-1"
  >
    <header class="weights-head">
      <h2 id="weights-heading" class="weights-title">Scoring weights</h2>
      <button
        type="button"
        class="btn btn-ghost weights-close"
        aria-label="Close scoring weights"
        @click="dismiss"
      >
        <AppIcon name="close" />
      </button>
    </header>

    <p class="weights-lede">
      How much each signal counts when recommendations are made. Nothing is written until you save.
    </p>

    <div class="weights-body">
      <p v-if="prefs.loading" class="state state--loading">
        <span class="spinner" aria-hidden="true" /> Loading your weights…
      </p>
      <p v-else-if="prefs.loadError" class="state state--error" role="alert">{{ prefs.loadError }}</p>
      <template v-else>
        <ScorerSlider
          v-for="key in SCORER_KEYS"
          :key="key"
          :label="formatScorerName(key)"
          :tooltip="SCORER_TOOLTIPS[key]"
          :model-value="prefs.getWeight(key)"
          @update:model-value="prefs.setWeight(key, $event)"
        />
        <ScorerSlider
          label="Variety After Completion"
          :tooltip="VARIETY_PENALTY_TOOLTIP"
          :model-value="prefs.varietyPenalty"
          @update:model-value="prefs.varietyPenalty = $event"
        />
      </template>
    </div>

    <footer class="weights-foot">
      <!-- Mounted while silent: a region inserted already populated is read as
           page content rather than announced (WCAG 4.1.3). -->
      <p
        class="weights-status"
        :class="{ failed: prefs.saveStatus === 'error' }"
        role="status"
      >{{ status }}</p>
      <div class="weights-actions">
        <button
          type="button"
          class="btn btn-secondary"
          :aria-disabled="opened === null || undefined"
          @click="revert"
        >Revert</button>
        <button
          type="button"
          class="btn btn-primary"
          :aria-disabled="prefs.saving || !prefs.hasLoaded || undefined"
          @click="save"
        >Save</button>
      </div>
    </footer>
  </aside>
</template>

<style scoped>
.weights-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-scrim);
  background: var(--overlay-scrim);
}

/* Inset on all four sides rather than flush, so it reads as an object over the
   app instead of a region of it, and the ranking stays visible beside it. */
.weights-panel {
  position: fixed;
  top: var(--space-4);
  right: var(--space-4);
  bottom: var(--space-4);
  z-index: var(--z-panel);
  display: flex;
  flex-direction: column;
  width: min(25rem, calc(100vw - var(--space-8)));
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-3);
  overflow: hidden;
}

.weights-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-3);
}

.weights-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  font-weight: var(--weight-medium);
  letter-spacing: var(--tracking-tight);
  color: var(--text-primary);
}

.weights-close {
  margin-left: auto;
  min-height: 44px;
  min-width: 44px;
}

.weights-lede {
  padding: 0 var(--space-5) var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.weights-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4) var(--space-5);
  box-shadow: inset 0 1px 0 var(--border-subtle);
}

/* The label column the Preferences page can afford would leave a 400px panel
   about five characters of track, so the slider takes its own line. */
.weights-body :deep(.slider-label) {
  width: auto;
  flex: 1;
  white-space: normal;
}

.weights-body :deep(.slider-row) {
  flex-wrap: wrap;
}

/* Ordered last so the reading row stays label, help, value. */
.weights-body :deep(input[type='range']) {
  order: 1;
  flex: 1 1 100%;
}

/* The body is a scroll container: above the trigger, or right of it, the tooltip
   lands in overflow no scroll reaches. Flat while hidden, so the last row's
   leaves no empty scroll under itself. */
.weights-body :deep(.scorer-tooltip-text) {
  top: calc(100% + 8px);
  bottom: auto;
  left: auto;
  right: 0;
  /* Running back from the trigger, it has the value column less room than the
     shared cap assumes, and a 320px viewport would clip the far edge (1.4.10). */
  max-width: min(16.25rem, calc(100vw - var(--space-12) * 2));
  max-height: 0;
  overflow: hidden;
}

.weights-body :deep(.scorer-tooltip-text::before) {
  top: auto;
  bottom: 100%;
}

.weights-body :deep(.scorer-tooltip-text::after) {
  top: auto;
  bottom: 100%;
  left: auto;
  right: var(--space-2);
  transform: translateX(50%);
  border-top-color: transparent;
  border-bottom-color: var(--bg-elevated);
}

.weights-body :deep(.scorer-tooltip-wrap:hover .scorer-tooltip-text),
.weights-body :deep(.scorer-tooltip-wrap:focus-within .scorer-tooltip-text),
.weights-body :deep(.scorer-tooltip-text:hover) {
  max-height: none;
  overflow: visible;
}

.weights-foot {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-5);
  box-shadow: inset 0 1px 0 var(--border-subtle);
}

/* Takes the leftover width so a long refusal wraps beside the buttons rather
   than shoving them off the row. */
.weights-status {
  flex: 1 1 10rem;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.weights-status.failed {
  color: var(--color-error-text);
}

.weights-actions {
  display: flex;
  gap: var(--space-2);
  margin-left: auto;
}

.weights-actions .btn {
  min-height: 44px;
}

@media (max-width: 768px) {
  /* Bottom-anchored on a phone: a right-docked panel would cover the list it
     is changing and sit out of thumb reach. */
  .weights-panel {
    top: auto;
    right: 0;
    left: 0;
    bottom: 0;
    width: auto;
    max-height: 84%;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  }
}
</style>
