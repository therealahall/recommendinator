<script setup lang="ts">
import { computed } from 'vue'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'

const props = withDefaults(defineProps<{
  modelValue: string
  includeAll?: boolean
  ariaLabel?: string
}>(), {
  includeAll: true,
  ariaLabel: 'Content type',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const visibleOptions = computed(() =>
  props.includeAll ? CONTENT_TYPE_OPTIONS : CONTENT_TYPE_OPTIONS.filter(o => o.value !== '')
)
</script>

<template>
  <select
    :value="modelValue"
    :aria-label="ariaLabel"
    @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option v-for="opt in visibleOptions" :key="opt.value" :value="opt.value">
      {{ opt.label }}
    </option>
  </select>
</template>
