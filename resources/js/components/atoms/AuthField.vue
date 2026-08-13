<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    id: string
    label: string
    modelValue: string
    /** Masking. With `autocomplete`, this is what a password manager reads. */
    type?: 'text' | 'password'
    autocomplete: string
    hint?: string
    /** id of the owning form's status region, when its message concerns this field. */
    describedBy?: string
    invalid?: boolean
    disabled?: boolean
    autofocus?: boolean
  }>(),
  {
    type: 'text',
    hint: '',
    describedBy: '',
    invalid: false,
    disabled: false,
    autofocus: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const input = ref<HTMLInputElement | null>(null)

const hintId = computed(() => (props.hint ? `${props.id}-hint` : ''))
const describedByIds = computed(
  () => [hintId.value, props.describedBy].filter(Boolean).join(' ') || undefined,
)

// iOS capitalises and autocorrects a plain text field, so a username typed
// one-handed on a phone arrives as "Aaron" and the sign-in is refused.
const literal = computed(() => props.autocomplete === 'username')

onMounted(() => {
  if (props.autofocus) input.value?.focus()
})
</script>

<template>
  <div class="auth-field">
    <label :for="id">{{ label }}</label>
    <input
      :id="id"
      ref="input"
      :type="type"
      :value="modelValue"
      :autocomplete="autocomplete"
      :autocapitalize="literal ? 'none' : undefined"
      :autocorrect="literal ? 'off' : undefined"
      :spellcheck="literal ? 'false' : undefined"
      :aria-describedby="describedByIds"
      :aria-invalid="invalid || undefined"
      :disabled="disabled"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <p v-if="hint" :id="hintId" class="auth-field-hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.auth-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.auth-field label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

/* --border-default is only 1.36:1 on a card, which leaves the field with no
   visible edge. --text-muted clears the 3:1 a control boundary needs. */
.auth-field input {
  width: 100%;
  /* 44px so the field is a thumb-sized target, and 1rem because anything under
     16px makes iOS Safari zoom the page on focus and strand the rest of the
     form off-screen — this is the surface someone reaches for on a phone. */
  min-height: 44px;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--text-muted);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 1rem;
}

.auth-field input[aria-invalid='true'] {
  border-color: var(--color-error);
}

.auth-field-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}
</style>
