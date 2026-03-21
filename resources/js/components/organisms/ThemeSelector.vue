<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onChange(event: Event) {
  const select = event.target as HTMLSelectElement
  emit('update:modelValue', select.value)
}
</script>

<template>
  <div v-if="theme.themes.length > 0" class="pref-section">
    <h3>Appearance</h3>
    <div class="dropdown-row">
      <span class="dropdown-label">Theme</span>
      <select
        class="theme-select"
        :value="modelValue"
        @change="onChange"
      >
        <option v-for="t in theme.themes" :key="t.id" :value="t.id">
          {{ t.name }}
        </option>
      </select>
    </div>
  </div>
</template>
