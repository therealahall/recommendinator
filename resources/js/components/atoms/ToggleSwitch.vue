<script setup lang="ts">
withDefaults(
  defineProps<{
    modelValue: boolean
    label: string
    /** id/aria hooks so a wrapping control can wire the switch to help/error text. */
    id?: string
    describedBy?: string
    invalid?: boolean
    /** Locks the switch while a save is in flight, like every other control. */
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()
</script>

<template>
  <label class="toggle-switch-label">
    <button
      :id="id"
      type="button"
      role="switch"
      class="toggle-switch"
      :aria-checked="modelValue"
      :aria-label="label"
      :aria-describedby="describedBy"
      :aria-invalid="invalid || undefined"
      :disabled="disabled"
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
