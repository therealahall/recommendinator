<script setup lang="ts">
const props = withDefaults(defineProps<{
  modelValue: number
  min?: number
  max?: number
  step?: number
  ariaLabel?: string
}>(), {
  min: 1,
  max: 100,
  step: 1,
  ariaLabel: 'Number',
})

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

function clamp(value: number): number {
  return Math.min(props.max, Math.max(props.min, value))
}

function decrement() {
  emit('update:modelValue', clamp(props.modelValue - props.step))
}

function increment() {
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
  <div class="number-stepper">
    <button
      type="button"
      class="stepper-btn stepper-decrement"
      :disabled="modelValue <= min"
      :aria-label="`Decrease ${ariaLabel}`"
      @click="decrement"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    </button>
    <input
      type="number"
      class="stepper-input"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :aria-label="ariaLabel"
      @input="onInput"
    >
    <button
      type="button"
      class="stepper-btn stepper-increment"
      :disabled="modelValue >= max"
      :aria-label="`Increase ${ariaLabel}`"
      @click="increment"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
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
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  overflow: hidden;
  height: 34px;
}

.stepper-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  background: var(--bg-elevated);
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
  padding: 0;
  font-family: inherit;
}

.stepper-btn:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.stepper-btn:disabled {
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
  width: 40px;
  text-align: center;
  background: var(--bg-input);
  border: none;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-family: var(--font-mono);
  padding: 0;
  -moz-appearance: textfield;
}

.stepper-input::-webkit-inner-spin-button,
.stepper-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.stepper-input:focus {
  outline: none;
  box-shadow: inset 0 0 0 1px var(--border-focus);
}
</style>
