<script setup lang="ts">
import { computed } from 'vue'

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

const options = [
  { value: '', label: 'All' },
  { value: 'book', label: 'Book' },
  { value: 'movie', label: 'Movie' },
  { value: 'tv_show', label: 'TV Show' },
  { value: 'video_game', label: 'Game' },
]

const visibleOptions = computed(() =>
  props.includeAll ? options : options.filter(o => o.value !== '')
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
