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
  <div class="pill-group" role="radiogroup" :aria-label="ariaLabel">
    <button
      v-for="opt in visibleOptions"
      :key="opt.value"
      type="button"
      role="radio"
      :aria-checked="modelValue === opt.value"
      :class="['pill', { active: modelValue === opt.value }]"
      @click="emit('update:modelValue', opt.value)"
    >{{ opt.label }}</button>
  </div>
</template>
