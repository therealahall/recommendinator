<script setup lang="ts">
import { usePreferencesStore, CONTENT_TYPES, LENGTH_OPTIONS } from '@/stores/preferences'
import { formatContentType, capitalize } from '@/utils/format'

const prefs = usePreferencesStore()

function getLength(type: string): string {
  return prefs.contentLengthPreferences[type] || 'any'
}

function setLength(type: string, value: string) {
  prefs.contentLengthPreferences[type] = value
}
</script>

<template>
  <div class="pref-section">
    <h3>Length Preferences</h3>
    <p class="help-text">Prefer short, medium, or long content per type.</p>
    <div v-for="type in CONTENT_TYPES" :key="type" class="dropdown-row">
      <label :for="`length-pref-${type}`" class="dropdown-label">{{ formatContentType(type) }}</label>
      <select
        :id="`length-pref-${type}`"
        class="length-select"
        :value="getLength(type)"
        @change="setLength(type, ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="opt in LENGTH_OPTIONS" :key="opt" :value="opt">
          {{ capitalize(opt) }}
        </option>
      </select>
    </div>
  </div>
</template>
