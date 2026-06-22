<script setup lang="ts">
import { computed } from 'vue'

const MAX_PERCENT = 80

const props = defineProps<{
  label: string
  tooltip?: string
  modelValue: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

const percent = computed(() => Math.round(props.modelValue * 100))
const displayValue = computed(() => (percent.value === 0 ? 'Off' : `${percent.value}%`))
const fillPercent = computed(() => `${(percent.value / MAX_PERCENT) * 100}%`)
const labelId = computed(() => {
  const slug = props.label.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
  return `slider-label-${slug}`
})

function onInput(event: Event) {
  const input = event.target as HTMLInputElement
  emit('update:modelValue', parseInt(input.value, 10) / 100)
}
</script>

<template>
  <div class="slider-row">
    <span :id="labelId" class="slider-label">
      {{ label }}
      <span
        v-if="tooltip"
        class="scorer-tooltip-wrap"
        tabindex="0"
        :aria-describedby="`${labelId}-tip`"
      >
        <span class="scorer-tooltip-icon" :aria-label="`${label} info`">?</span>
        <span :id="`${labelId}-tip`" class="scorer-tooltip-text" role="tooltip">{{ tooltip }}</span>
      </span>
    </span>
    <input
      type="range"
      min="0"
      :max="MAX_PERCENT"
      step="5"
      :value="percent"
      class="pref-slider"
      :style="{ '--value-percent': fillPercent }"
      :aria-labelledby="labelId"
      :aria-valuetext="displayValue"
      @input="onInput"
    >
    <span class="slider-value" aria-hidden="true">{{ displayValue }}</span>
  </div>
</template>
