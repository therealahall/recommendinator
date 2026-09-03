<script setup lang="ts">
import { computed, useAttrs } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  modelValue: number | null
  min?: number
  max?: number
  step?: number
  id?: string
  describedBy?: string
  invalid?: boolean
  disabled?: boolean
}>(), {
  step: 1,
  disabled: false,
})
// Neither `min` nor `max` has a default, and `null` is "no value yet": a control must
// never name a bound or a number its caller has not declared, so a press names `min`.

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
const atMin = computed(
  () => props.min != null && props.modelValue != null && props.modelValue <= props.min,
)
const atMax = computed(
  () => props.max != null && props.modelValue != null && props.modelValue >= props.max,
)

function stepped(delta: number): number {
  if (props.modelValue == null) return clamp(props.min ?? 0)
  return clamp(props.modelValue + delta)
}

function decrement() {
  if (props.disabled || atMin.value) return
  emit('update:modelValue', stepped(-props.step))
}

function increment() {
  if (props.disabled || atMax.value) return
  emit('update:modelValue', stepped(props.step))
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
  <div class="number-stepper field" v-bind="filteredAttrs">
    <button
      type="button"
      class="stepper-btn stepper-decrement"
      :disabled="disabled"
      :aria-disabled="atMin || undefined"
      :aria-label="`Decrease ${resolvedLabel}`"
      @click="decrement"
    >
      <AppIcon name="minus" />
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
      <AppIcon name="plus" />
    </button>
  </div>
</template>

<style scoped>
/* The buttons meet the edge, so the shared field padding would strand a strip
   of --bg-input; an auto width would stretch to the column, not to the digits. */
.number-stepper {
  display: inline-flex;
  align-items: stretch;
  width: min(var(--field-num-w), 100%);
  padding: 0;
  overflow: hidden;
  /* min-height, not height: under text-only zoom the line box outgrows the
     floor and `overflow: hidden` would clip the digits (WCAG 1.4.4). */
  min-height: 44px;
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

/* Inset: the clip above cuts the shared outward ring off a full-bleed button. */
.stepper-btn:focus-visible {
  outline: 2px solid var(--accent-light);
  outline-offset: -2px;
}

.stepper-decrement {
  border-right: 1px solid var(--border-default);
}

.stepper-increment {
  border-left: 1px solid var(--border-default);
}

.stepper-input {
  flex: 1;
  min-width: 0;
  text-align: center;
  background: var(--bg-input);
  border: none;
  color: var(--text-primary);
  font-size: var(--control-text);
  font-variant-numeric: tabular-nums;
  padding: 0 var(--space-1);
  -moz-appearance: textfield;
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
