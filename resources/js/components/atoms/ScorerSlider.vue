<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  tooltip?: string
  modelValue: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

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
    <span :id="labelId" class="slider-label">
      {{ label }}
      <span v-if="tooltip" class="scorer-tooltip-wrap" tabindex="0">
        <span class="scorer-tooltip-icon" :aria-label="`${label} info`">?</span>
        <span class="scorer-tooltip-text">{{ tooltip }}</span>
      </span>
    </span>
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
