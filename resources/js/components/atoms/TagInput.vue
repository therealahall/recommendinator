<script setup lang="ts">
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string[]
  label: string
  inputId: string
  addButtonLabel?: string
  placeholder?: string
  emptyText?: string
}>(), {
  addButtonLabel: 'Add',
  placeholder: '',
  emptyText: 'None yet',
})

const emit = defineEmits<{
  'update:modelValue': [value: string[]]
}>()

const draft = ref('')

// The web API rejects genre/tag strings longer than this with a 422.
const MAX_LENGTH = 100

function add() {
  const value = draft.value.trim()
  if (!value || value.length > MAX_LENGTH) return
  if (props.modelValue.some((tag) => tag.toLowerCase() === value.toLowerCase())) {
    draft.value = ''
    return
  }
  emit('update:modelValue', [...props.modelValue, value])
  draft.value = ''
}

function remove(index: number) {
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

function onKeypress(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    event.preventDefault()
    add()
  }
}
</script>

<template>
  <div class="tag-input">
    <label :for="inputId">{{ label }}</label>
    <div v-if="modelValue.length === 0" class="empty-rules">{{ emptyText }}</div>
    <div v-else class="tag-input-chips">
      <span v-for="(tag, index) in modelValue" :key="tag" class="profile-tag tag-input-chip">
        {{ tag }}
        <button
          type="button"
          class="tag-input-remove"
          :aria-label="`Remove ${tag}`"
          @click="remove(index)"
        >×</button>
      </span>
    </div>
    <div class="add-rule-form">
      <input
        :id="inputId"
        type="text"
        v-model="draft"
        :placeholder="placeholder"
        :maxlength="MAX_LENGTH"
        @keypress="onKeypress"
      >
      <button type="button" class="btn btn-small btn-primary" @click="add">{{ addButtonLabel }}</button>
    </div>
  </div>
</template>

<style scoped>
.tag-input label {
  display: block;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: var(--space-1);
}

.tag-input-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.tag-input-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.tag-input-remove {
  border: none;
  background: none;
  color: inherit;
  font-size: var(--text-md);
  line-height: 1;
  padding: 0;
  cursor: pointer;
}
</style>
