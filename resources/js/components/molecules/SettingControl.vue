<script setup lang="ts">
import { computed } from 'vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import NumberStepper from '@/components/atoms/NumberStepper.vue'
import TagInput from '@/components/atoms/TagInput.vue'
import type { SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    setting: SettingViewValue
    modelValue: string | number | boolean | string[] | null
    disabled?: boolean
    /** Inline validation message from a 422; presence marks the field invalid. */
    error?: string
    resetting?: boolean
  }>(),
  { disabled: false, error: '', resetting: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | number | boolean | string[]]
  reset: []
}>()

const KNOWN_WIDGETS = ['toggle', 'number', 'text', 'tags', 'select']

// Resolve the widget to render. Known widgets map directly; an unknown widget
// falls back on the setting's `type` so every leaf still gets a usable control.
const control = computed<
  'toggle' | 'number-int' | 'number-float' | 'text' | 'tags' | 'select'
>(() => {
  const setting = props.setting
  let widget = setting.widget as string
  if (!KNOWN_WIDGETS.includes(widget)) {
    widget =
      { bool: 'toggle', int: 'number', float: 'number', list: 'tags', enum: 'select', string: 'text' }[
        setting.type
      ] ?? 'text'
  }
  if (widget === 'number') return setting.type === 'float' ? 'number-float' : 'number-int'
  if (widget === 'select' && !setting.choices) return 'text'
  return widget as 'toggle' | 'text' | 'tags' | 'select'
})

const invalid = computed(() => Boolean(props.error))
const inputId = computed(() => `setting-${props.setting.key}`)
const resetLockId = computed(() => `reset-locked-${props.setting.key}`)
const helpId = computed(() => (props.setting.help ? `help-${props.setting.key}` : ''))
const errId = computed(() => `err-${props.setting.key}`)
const describedBy = computed(() => {
  const ids: string[] = []
  if (helpId.value) ids.push(helpId.value)
  if (invalid.value) ids.push(errId.value)
  return ids.join(' ') || undefined
})

const currentStr = computed(() =>
  typeof props.modelValue === 'string' ? props.modelValue : '',
)

// Always render the current value as an option so an out-of-range stored value
// isn't silently dropped from the select.
const selectChoices = computed(() => {
  const choices = props.setting.choices ?? []
  if (currentStr.value && !choices.includes(currentStr.value)) {
    return [currentStr.value, ...choices]
  }
  return choices
})

const validation = computed(() => props.setting.validation)

// aria-disabled, not disabled: the lock closes on the button the user has just
// activated, and a disabled button is blurred and unreachable by Tab, so the
// reason it refuses is never read out (WCAG 2.4.3).
function onReset(): void {
  if (props.disabled || props.resetting) return
  emit('reset')
}

function onFloatInput(event: Event): void {
  const parsedNumber = parseFloat((event.target as HTMLInputElement).value)
  emit('update:modelValue', Number.isFinite(parsedNumber) ? parsedNumber : 0)
}

function onFloatBlur(event: Event): void {
  const inputElement = event.target as HTMLInputElement
  if (inputElement.value === '') return
  let parsedNumber = parseFloat(inputElement.value)
  if (!Number.isFinite(parsedNumber)) return
  const constraints = validation.value
  if (constraints?.min != null) parsedNumber = Math.max(constraints.min, parsedNumber)
  if (constraints?.max != null) parsedNumber = Math.min(constraints.max, parsedNumber)
  emit('update:modelValue', parsedNumber)
}
</script>

<template>
  <div class="setting-control source-form-field">
    <!-- Toggle: the atom renders its own visible label + aria-label. -->
    <template v-if="control === 'toggle'">
      <ToggleSwitch
        :id="inputId"
        :model-value="modelValue as boolean"
        :label="setting.label"
        :described-by="describedBy"
        :invalid="invalid"
        :disabled="disabled"
        :data-testid="`setting-${setting.key}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>

    <!-- Integer: NumberStepper (int-only atom). The visible <label for> gives
         click-to-focus, matching every other branch; the aria-label stays
         because NumberStepper derives its "Increase X"/"Decrease X" button
         names from it. -->
    <template v-else-if="control === 'number-int'">
      <label :for="inputId" class="source-form-label">{{ setting.label }}</label>
      <NumberStepper
        :id="inputId"
        :model-value="(modelValue as number) ?? 0"
        :min="validation?.min ?? undefined"
        :max="validation?.max ?? undefined"
        :aria-label="setting.label"
        :described-by="describedBy"
        :invalid="invalid"
        :disabled="disabled"
        :data-testid="`setting-${setting.key}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>

    <!-- Float: plain number input with step="any" + clamp on blur. -->
    <template v-else-if="control === 'number-float'">
      <label :for="inputId" class="source-form-label">{{ setting.label }}</label>
      <input
        :id="inputId"
        type="number"
        step="any"
        class="field"
        :min="validation?.min ?? undefined"
        :max="validation?.max ?? undefined"
        :value="modelValue as number"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :aria-describedby="describedBy"
        :data-testid="`setting-${setting.key}`"
        @input="onFloatInput"
        @blur="onFloatBlur"
      />
    </template>

    <!-- Tags: the atom renders its own <label for>. -->
    <template v-else-if="control === 'tags'">
      <TagInput
        :model-value="(modelValue as string[]) ?? []"
        :label="setting.label"
        :input-id="inputId"
        :described-by="describedBy"
        :invalid="invalid"
        :disabled="disabled"
        :data-testid="`setting-${setting.key}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>

    <!-- Select: styled dropdown, choices always include the current value. -->
    <template v-else-if="control === 'select'">
      <label :for="inputId" class="source-form-label">{{ setting.label }}</label>
      <select
        :id="inputId"
        class="field"
        :value="modelValue as string"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :aria-describedby="describedBy"
        :data-testid="`setting-${setting.key}`"
        @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="choice in selectChoices" :key="choice" :value="choice">{{ choice }}</option>
      </select>
    </template>

    <!-- Text (also the fallback for unknown widgets). -->
    <template v-else>
      <label :for="inputId" class="source-form-label">{{ setting.label }}</label>
      <input
        :id="inputId"
        type="text"
        class="field"
        :maxlength="validation?.max_length ?? undefined"
        :pattern="validation?.pattern ?? undefined"
        :value="modelValue as string"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :aria-describedby="describedBy"
        :data-testid="`setting-${setting.key}`"
        @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
      />
    </template>

    <p v-if="setting.help" :id="`help-${setting.key}`" class="source-form-help">
      {{ setting.help }}
    </p>

    <p
      v-if="invalid"
      :id="errId"
      class="state state--error"
      role="alert"
      :data-testid="`setting-error-${setting.key}`"
    >{{ error }}</p>

    <div class="setting-row-meta">
      <span
        v-if="setting.restart_required"
        class="badge"
        data-tone="warning"
        title="This setting takes effect after a restart"
        :data-testid="`restart-badge-${setting.key}`"
      >Requires restart<span class="sr-only"> to take effect</span></span>
      <span
        v-if="setting.db_overridden"
        class="badge"
        data-tone="accent"
        :data-testid="`overridden-badge-${setting.key}`"
      >Overridden<span class="sr-only"> — differs from the built-in default</span></span>
      <button
        v-if="setting.has_stored_value"
        type="button"
        class="btn btn-secondary btn-small"
        :aria-disabled="disabled || resetting || undefined"
        :aria-describedby="disabled && !resetting ? resetLockId : undefined"
        :data-testid="`reset-${setting.key}`"
        @click="onReset"
      >{{ resetting ? 'Resetting…' : 'Reset to default' }}<span class="sr-only"> — {{ setting.label }}</span></button>
      <span v-if="disabled && !resetting" :id="resetLockId" class="sr-only"
        >Unavailable while this section is saving.</span
      >
    </div>
  </div>
</template>

<style scoped>
.setting-control {
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-subtle);
}

.setting-control:last-child {
  border-bottom: none;
}

/* How long the value can run is what picks a width here, never which widget the
   registry rendered it with, so the column has one right edge and not six. */
.setting-control > input.field,
.setting-control > select.field,
.setting-control > .tag-input {
  align-self: flex-start;
  width: min(var(--field-w), 100%);
}

.setting-control > input[type='number'].field {
  width: min(var(--field-num-w), 100%);
}

.setting-row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.setting-row-meta:empty {
  display: none;
}

/* .badge, .field and .state are shared primitives in base.css. */
</style>
