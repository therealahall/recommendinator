<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useAnnouncer } from '@/composables/useAnnouncer'

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
  /** Offers each chip a move up/down button, for a list whose order is its meaning. */
  reorderable?: boolean
}>(), {
  addButtonLabel: 'Add',
  placeholder: '',
  emptyText: 'None yet',
  disabled: false,
  reorderable: false,
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
const pendingFocus = ref<{ index: number; selector: string } | null>(null)
const { message, announce } = useAnnouncer()

const REMOVE = '.tag-input-remove'
const MOVE_UP = '.tag-input-move[data-direction="up"]'
const MOVE_DOWN = '.tag-input-move[data-direction="down"]'

function remove(index: number) {
  if (props.disabled) return
  // Record the intent only. This is a controlled component: the chip does not
  // disappear until the parent applies the new array, so moving focus here
  // would query a DOM that still holds the old chips.
  pendingFocus.value = { index, selector: REMOVE }
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

function move(index: number, step: -1 | 1) {
  const target = index + step
  if (props.disabled || target < 0 || target >= props.modelValue.length) return
  const reordered = [...props.modelValue]
  const [moved] = reordered.splice(index, 1)
  reordered.splice(target, 0, moved)
  pendingFocus.value = { index: target, selector: step < 0 ? MOVE_UP : MOVE_DOWN }
  emit('update:modelValue', reordered)
  void announce(`${moved} moved to position ${target + 1} of ${reordered.length}`)
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
    const pending = pendingFocus.value
    if (pending === null) return
    pendingFocus.value = null
    await nextTick()
    // Land on whichever chip slid into that position, or the draft input once
    // the last one is gone.
    const buttons = chipList.value?.querySelectorAll<HTMLElement>(pending.selector)
    const next = buttons?.length
      ? buttons[Math.min(pending.index, buttons.length - 1)]
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
    <!-- Explicit role="list": the `list-style: none` below drops list semantics
         in Safari/VoiceOver, and position-of-length is the only way to read the
         order back on demand once the status announcement has passed. -->
    <ol v-else ref="chipList" class="tag-input-chips" role="list">
      <li v-for="(tag, index) in modelValue" :key="tag">
        <span class="badge" data-tone="accent">
          {{ tag }}
          <!-- aria-disabled at the ends, not disabled: the button the user just
               activated to reach position 1 would otherwise blur and leave them
               tabbing from the top of the page (WCAG 2.4.3). -->
          <button
            v-if="reorderable"
            type="button"
            class="tag-input-move"
            data-direction="up"
            :aria-label="`Move ${tag} up`"
            :aria-disabled="disabled || index === 0 || undefined"
            @click="move(index, -1)"
          >↑</button>
          <button
            v-if="reorderable"
            type="button"
            class="tag-input-move"
            data-direction="down"
            :aria-label="`Move ${tag} down`"
            :aria-disabled="disabled || index === modelValue.length - 1 || undefined"
            @click="move(index, 1)"
          >↓</button>
          <button
            type="button"
            class="tag-input-remove"
            :aria-label="`Remove ${tag}`"
            :disabled="disabled"
            @click="remove(index)"
          >×</button>
        </span>
      </li>
    </ol>
    <!-- Mounted for the whole life of a reorderable list, never toggled by its
         text: a region that first appears already populated is read as page
         content rather than as a status change (WCAG 4.1.3). -->
    <p
      v-if="reorderable"
      class="sr-only"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ message }}</p>
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
  font-weight: var(--weight-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--space-1);
}

.tag-input-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  list-style: none;
  margin: 0;
  padding: 0;
}

.tag-input-remove,
.tag-input-move {
  align-items: center;
  border: none;
  background: none;
  color: inherit;
  display: inline-flex;
  font-size: var(--text-md);
  justify-content: center;
  line-height: 1;
  min-height: 24px;
  min-width: 24px;
  padding: 0;
  cursor: pointer;
}

/* These are not .btn, so the global disabled rule misses them and an inert
   arrow reads as a live one. */
.tag-input-remove:disabled,
.tag-input-move:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* Its own fill, never a fade (base.css:853): aria-disabled stays focusable, so
   a low-vision operator has to resolve which arrowhead is refused. */
.tag-input-remove[aria-disabled='true'],
.tag-input-move[aria-disabled='true'] {
  color: var(--text-muted);
  cursor: not-allowed;
}
</style>
