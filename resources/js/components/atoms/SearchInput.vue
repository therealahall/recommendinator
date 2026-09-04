<script setup lang="ts">
import { ref, computed } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'

const props = withDefaults(defineProps<{
  modelValue: string
  loading?: boolean
  id?: string
  label?: string
  placeholder?: string
  maxlength?: number
}>(), {
  loading: false,
  id: 'library-search',
  label: 'Search library',
  placeholder: '',
  maxlength: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  clear: []
}>()

const input = ref<HTMLInputElement | null>(null)

const atLimit = computed(
  () => props.maxlength !== undefined && props.modelValue.length >= props.maxlength,
)
const limitId = computed(() => `${props.id}-limit`)
const limitMessage = computed(
  () => `Search is limited to ${props.maxlength} characters. Anything longer is not included.`,
)
const limitAnnouncement = computed(() => (atLimit.value ? limitMessage.value : ''))

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
  <div class="search-input-wrap" role="search">
    <div class="search-input field">
      <label :for="id" class="sr-only">{{ label }}</label>
      <AppIcon name="search" class="search-input-icon" />
      <input
        :id="id"
        ref="input"
        type="search"
        class="search-input-field"
        :value="modelValue"
        :placeholder="placeholder"
        autocomplete="off"
        enterkeyhint="search"
        :maxlength="maxlength"
        :aria-busy="loading"
        :aria-describedby="atLimit ? limitId : undefined"
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
          <AppIcon name="close" />
        </button>
      </span>
    </div>
    <p v-if="atLimit" :id="limitId" class="search-input-limit">{{ limitMessage }}</p>
    <!-- The visible notice above carries no live role, or reaching
         the limit would announce twice. -->
    <!--
      aria-live regions must exist in the DOM before content arrives, otherwise
      some screen readers (notably JAWS) skip the announcement when the region
      is inserted already populated — the user would hit the cap in silence
      (WCAG 4.1.3 status messages).
    -->
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ limitAnnouncement }}</p>
  </div>
</template>

<style scoped>
.search-input-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.search-input-limit {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* The box is .field; this is the row inside it holding the glyph, the entry and
   the clear button, none of which draws an edge of its own. */
.search-input {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  transition: border-color var(--transition-fast);
}

.search-input:focus-within {
  border-color: var(--border-focus);
}

.search-input:has(:focus-visible) {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.search-input-icon {
  color: var(--text-muted);
}

.search-input-field {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--control-text);
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

/* Qualified by the field so it outranks the 44px floor a toolbar row sets. */
.search-input .search-input-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  padding: 0;
}
</style>
