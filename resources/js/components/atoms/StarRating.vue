<script setup lang="ts">
import AppIcon from '@/components/atoms/AppIcon.vue'

const props = defineProps<{
  modelValue: number | null
  ariaLabelledby?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number | null]
}>()

function counted(star: number): boolean {
  return props.modelValue !== null && star <= props.modelValue
}

function setRating(value: number) {
  emit('update:modelValue', value)
}

function clear() {
  emit('update:modelValue', null)
}
</script>

<template>
  <div class="star-rating" role="group" :aria-labelledby="ariaLabelledby || undefined" :aria-label="ariaLabelledby ? undefined : 'Rating'">
    <div class="star-rating-stars">
      <button
        v-for="star in 5"
        :key="star"
        type="button"
        class="star-rating-star"
        :class="{ active: counted(star) }"
        :aria-label="`${star} star${star !== 1 ? 's' : ''}`"
        :aria-pressed="counted(star)"
        @click="setRating(star)"
      ><AppIcon :name="counted(star) ? 'star-filled' : 'star'" /></button>
    </div>
    <button class="btn btn-small btn-clear-rating" type="button" @click="clear">Clear</button>
  </div>
</template>

<style scoped>
.star-rating {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.star-rating-stars {
  display: flex;
  gap: 2px;
}

.star-rating-star {
  font-size: 1.25rem;
  color: var(--border-interactive);
  cursor: pointer;
  background: none;
  border: none;
  padding: 2px;
  line-height: 1;
  transition: color var(--transition-fast);
}

.star-rating-star .icon {
  width: 1em;
  height: 1em;
}

.star-rating-star.active {
  color: var(--color-warning);
}

.star-rating-star:hover {
  color: var(--accent-hover);
}

.btn-clear-rating {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid var(--border-interactive);
}
</style>
