<script setup lang="ts">
import { computed, useAttrs } from 'vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  modelValue: number
  min?: number
  max?: number
  step?: number
  /** id/aria hooks so a wrapping control can wire the input to help/error text. */
  id?: string
  describedBy?: string
  invalid?: boolean
  /** Locks the control while a save is in flight, matching the other controls. */
  disabled?: boolean
}>(), {
  step: 1,
  disabled: false,
})
// Neither `min` nor `max` has a default: a control must never report or enforce a
// bound its registry entry does not declare.

const attrs = useAttrs()
const resolvedLabel = computed(() =>
  (attrs['aria-label'] as string | undefined) ?? 'Number'
)
const filteredAttrs = computed(() => {
  const { 'aria-label': _, ...rest } = attrs
  return rest
})

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

function clamp(value: number): number {
  const lower = props.min ?? Number.NEGATIVE_INFINITY
  const upper = props.max ?? Number.POSITIVE_INFINITY
  return Math.min(upper, Math.max(lower, value))
}

// Bound state uses aria-disabled, NOT native disabled: stepping down to `min`
// while the Decrease button has focus would blur the element the user is
// operating and drop focus to <body> mid-interaction (WCAG 2.4.3).
const atMin = computed(() => props.min != null && props.modelValue <= props.min)
const atMax = computed(() => props.max != null && props.modelValue >= props.max)

function decrement() {
  if (props.disabled || atMin.value) return
  emit('update:modelValue', clamp(props.modelValue - props.step))
}

function increment() {
  if (props.disabled || atMax.value) return
  emit('update:modelValue', clamp(props.modelValue + props.step))
}

function onInput(event: Event) {
  const input = event.target as HTMLInputElement
  const parsed = parseInt(input.value, 10)
  if (!isNaN(parsed)) {
    emit('update:modelValue', clamp(parsed))
  }
}
</script>

<template>
  <div class="number-stepper" v-bind="filteredAttrs">
    <button
      type="button"
      class="stepper-btn stepper-decrement"
      :disabled="disabled"
      :aria-disabled="atMin || undefined"
      :aria-label="`Decrease ${resolvedLabel}`"
      @click="decrement"
    >
      <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    </button>
    <input
      :id="id"
      type="number"
      class="stepper-input"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :aria-label="resolvedLabel"
      :aria-describedby="describedBy"
      :aria-invalid="invalid || undefined"
      :disabled="disabled"
      @input="onInput"
    >
    <button
      type="button"
      class="stepper-btn stepper-increment"
      :disabled="disabled"
      :aria-disabled="atMax || undefined"
      :aria-label="`Increase ${resolvedLabel}`"
      @click="increment"
    >
      <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.number-stepper {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-md);
  overflow: hidden;
  /* min-height, not height: font-size is rem-derived, so under text-only zoom
     the line box outgrows a fixed 34px and `overflow: hidden` would clip the
     digits — the same 1.4.4 concern the width above is sized for. */
  min-height: 34px;
}

.stepper-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  background: var(--bg-elevated);
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
  padding: 0;
  font-family: inherit;
}

.stepper-btn:hover:not(:disabled):not([aria-disabled='true']) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* Both forms look the same: aria-disabled marks "at the bound" (still
   focusable), native disabled marks "save in flight". */
.stepper-btn:disabled,
.stepper-btn[aria-disabled='true'] {
  opacity: 0.3;
  cursor: not-allowed;
}

.stepper-decrement {
  border-right: 1px solid var(--border-default);
}

.stepper-increment {
  border-left: 1px solid var(--border-default);
}

.stepper-input {
  /* 7ch sizes a six-digit value; one registry int leaf declares a max, of 5.
     ch, not px: it scales with font size (1.4.4). The base.css
     `box-sizing: border-box` reset makes `width` include the horizontal
     padding, so it is added back. */
  width: calc(7ch + var(--space-1) * 2);
  text-align: center;
  background: var(--bg-input);
  border: none;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  padding: 0 var(--space-1);
  -moz-appearance: textfield;
}

/* The @supports gate matters — without field-sizing, `width: auto` on a
   number input resolves to the intrinsic ~20-character width and the stepper
   balloons instead. */
@supports (field-sizing: content) {
  .stepper-input {
    width: auto;
    min-width: calc(4ch + var(--space-1) * 2);
    /* Bounded: min-only leaves (e.g. recommendations.max_count) clamp against
       +Infinity, so an over-long paste would otherwise grow the stepper past a
       320px viewport and break reflow. */
    max-width: calc(12ch + var(--space-1) * 2);
    field-sizing: content;
  }
}

.stepper-input:disabled {
  /* The UA default greys disabled number text below what a low-vision user can
     resolve, and -webkit-text-fill-color wins over color. Fading it back with
     opacity would undo that; the buttons and cursor already convey the inert
     state. */
  color: var(--text-primary);
  -webkit-text-fill-color: var(--text-primary);
  cursor: not-allowed;
}

.stepper-input::-webkit-inner-spin-button,
.stepper-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.stepper-input:focus-visible {
  outline: 2px solid var(--accent-light);
  outline-offset: -2px;
}
</style>
