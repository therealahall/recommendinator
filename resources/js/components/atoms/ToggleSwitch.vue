<script setup lang="ts">
defineProps<{
  modelValue: boolean
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()
</script>

<template>
  <label class="toggle-switch-label">
    <button
      type="button"
      role="switch"
      class="toggle-switch"
      :aria-checked="modelValue"
      :aria-label="label"
      @click="emit('update:modelValue', !modelValue)"
    />
    <span class="toggle-switch-text">{{ label }}</span>
  </label>
</template>

<style scoped>
.toggle-switch-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.toggle-switch-text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  white-space: nowrap;
}
</style>
