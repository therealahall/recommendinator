<script setup lang="ts">
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string
  loading?: boolean
  id?: string
  label?: string
  placeholder?: string
}>(), {
  loading: false,
  id: 'library-search',
  label: 'Search library',
  placeholder: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  clear: []
}>()

const input = ref<HTMLInputElement | null>(null)

function onInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).value)
}

function onClear() {
  emit('update:modelValue', '')
  emit('clear')
  input.value?.focus()
}

function onEscape(e: KeyboardEvent) {
  if (props.modelValue.length > 0) {
    e.preventDefault()
    onClear()
  }
}
</script>

<template>
  <div class="search-input" role="search">
    <label :for="id" class="sr-only">{{ label }}</label>
    <svg
      class="search-input-icon"
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
    <input
      :id="id"
      ref="input"
      type="search"
      class="search-input-field"
      :value="modelValue"
      :placeholder="placeholder"
      autocomplete="off"
      enterkeyhint="search"
      :aria-busy="loading"
      @input="onInput"
      @keydown.esc="onEscape"
    >
    <span class="search-input-trailing">
      <span v-if="loading" class="spinner" aria-hidden="true" />
      <button
        v-else-if="modelValue.length > 0"
        type="button"
        class="btn btn-ghost search-input-clear"
        aria-label="Clear search"
        @click="onClear"
      >
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </span>
  </div>
</template>

<style scoped>
.search-input {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  transition: border-color var(--transition-fast);
}

.search-input:focus-within {
  border-color: var(--border-focus);
}

.search-input:has(:focus-visible) {
  outline: 2px solid var(--accent-light);
  outline-offset: 2px;
}

.search-input-icon {
  flex-shrink: 0;
  color: var(--text-muted);
}

.search-input-field {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-family: inherit;
}

.search-input-field::placeholder {
  color: var(--text-muted);
}

.search-input-field:focus-visible {
  outline: none;
}

.search-input-field::-webkit-search-cancel-button {
  display: none;
}

.search-input-trailing {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  flex-shrink: 0;
}

.search-input-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
}
</style>
