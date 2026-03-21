<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()

function onChange(event: Event) {
  const select = event.target as HTMLSelectElement
  theme.applyTheme(select.value)
}
</script>

<template>
  <div v-if="theme.themes.length > 0" class="pref-section">
    <h3>Appearance</h3>
    <div class="dropdown-row">
      <span class="dropdown-label">Theme</span>
      <select
        class="theme-select"
        :value="theme.currentThemeId || theme.defaultThemeId"
        @change="onChange"
      >
        <option v-for="t in theme.themes" :key="t.id" :value="t.id">
          {{ t.name }}
        </option>
      </select>
    </div>
  </div>
</template>
