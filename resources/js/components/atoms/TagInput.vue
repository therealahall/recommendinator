<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string[]
  label: string
  inputId: string
  addButtonLabel?: string
  placeholder?: string
  emptyText?: string
  /** aria hooks so a wrapping control can wire the draft input to help/error text. */
  describedBy?: string
  invalid?: boolean
  /** Locks the input while a save is in flight, like every other control. */
  disabled?: boolean
}>(), {
  addButtonLabel: 'Add',
  placeholder: '',
  emptyText: 'None yet',
  disabled: false,
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

const chipList = ref<HTMLElement | null>(null)
const draftInput = ref<HTMLInputElement | null>(null)
const pendingFocusIndex = ref<number | null>(null)

function remove(index: number) {
  if (props.disabled) return
  // Record the intent only. This is a controlled component: the chip does not
  // disappear until the parent applies the new array, so moving focus here
  // would query a DOM that still holds the old chips.
  pendingFocusIndex.value = index
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

// The × button the user activated unmounts with its chip, so focus would fall
// to <body> and their next Tab would restart at the top of the document — once
// per chip while pruning a list (WCAG 2.4.3).
watch(
  // Driven off the prop rather than the click so focus only moves if the
  // removal actually took; a parent that rejects the change leaves focus where
  // the user put it.
  () => props.modelValue,
  async () => {
    if (pendingFocusIndex.value === null) return
    const index = pendingFocusIndex.value
    pendingFocusIndex.value = null
    await nextTick()
    // Land on whichever chip slid into that position, or the draft input once
    // the last one is gone.
    const buttons = chipList.value?.querySelectorAll<HTMLElement>('.tag-input-remove')
    const next = buttons?.length
      ? buttons[Math.min(index, buttons.length - 1)]
      : undefined
    ;(next ?? draftInput.value)?.focus()
  },
)

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
    <div v-if="modelValue.length === 0" class="state">{{ emptyText }}</div>
    <div v-else ref="chipList" class="tag-input-chips">
      <span v-for="(tag, index) in modelValue" :key="tag" class="badge" data-tone="accent">
        {{ tag }}
        <button
          type="button"
          class="tag-input-remove"
          :aria-label="`Remove ${tag}`"
          :disabled="disabled"
          @click="remove(index)"
        >×</button>
      </span>
    </div>
    <div class="add-rule-form">
      <input
        :id="inputId"
        ref="draftInput"
        type="text"
        class="field"
        v-model="draft"
        :placeholder="placeholder"
        :maxlength="MAX_LENGTH"
        :aria-describedby="describedBy"
        :aria-invalid="invalid || undefined"
        :disabled="disabled"
        @keypress="onKeypress"
      >
      <button
        type="button"
        class="btn btn-small btn-primary"
        :disabled="disabled"
        @click="add"
      >{{ addButtonLabel }}</button>
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
