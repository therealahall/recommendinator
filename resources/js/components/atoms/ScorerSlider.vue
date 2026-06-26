<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  label: string
  tooltip?: string
  modelValue: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

// WCAG 1.4.13 Dismissable: Escape hides the tooltip without moving the
// pointer or focus. mouseleave/blur clear it so it can reappear next time.
const dismissed = ref(false)

const displayValue = computed(() => props.modelValue.toFixed(1))
const fillPercent = computed(() => `${(props.modelValue / 5) * 100}%`)
const labelId = computed(() => {
  const slug = props.label.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
  return `slider-label-${slug}`
})

function onInput(event: Event) {
  const input = event.target as HTMLInputElement
  emit('update:modelValue', parseFloat(input.value))
}
</script>

<template>
  <div class="slider-row">
    <span :id="labelId" class="slider-label">{{ label }}</span>
    <button
      v-if="tooltip"
      type="button"
      class="scorer-tooltip-wrap"
      :class="{ 'tooltip-dismissed': dismissed }"
      :aria-label="`${label} info`"
      :aria-describedby="`${labelId}-tip`"
      @keydown.esc.stop.prevent="dismissed = true"
      @mouseleave="dismissed = false"
      @blur="dismissed = false"
    >
      <span class="scorer-tooltip-icon" aria-hidden="true">?</span>
      <span :id="`${labelId}-tip`" class="scorer-tooltip-text" role="tooltip">{{ tooltip }}</span>
    </button>
    <input
      type="range"
      min="0"
      max="5"
      step="0.1"
      :value="modelValue"
      class="pref-slider"
      :style="{ '--value-percent': fillPercent }"
      :aria-labelledby="labelId"
      :aria-valuetext="displayValue"
      @input="onInput"
    >
    <span class="slider-value" aria-hidden="true">{{ displayValue }}</span>
  </div>
</template>
