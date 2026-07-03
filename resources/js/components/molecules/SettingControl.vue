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
        :data-testid="`setting-${setting.key}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>

    <!-- Integer: NumberStepper (int-only atom), labelled via aria-label. -->
    <template v-else-if="control === 'number-int'">
      <span class="source-form-label">{{ setting.label }}</span>
      <NumberStepper
        :id="inputId"
        :model-value="(modelValue as number) ?? 0"
        :min="validation?.min ?? undefined"
        :max="validation?.max ?? undefined"
        :aria-label="setting.label"
        :described-by="describedBy"
        :invalid="invalid"
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
        :min="validation?.min ?? undefined"
        :max="validation?.max ?? undefined"
        :value="modelValue as number"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :class="{ 'setting-input--invalid': invalid }"
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
        :data-testid="`setting-${setting.key}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>

    <!-- Select: styled dropdown, choices always include the current value. -->
    <template v-else-if="control === 'select'">
      <label :for="inputId" class="source-form-label">{{ setting.label }}</label>
      <select
        :id="inputId"
        class="theme-select setting-select"
        :value="modelValue as string"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :class="{ 'setting-input--invalid': invalid }"
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
        :maxlength="validation?.max_length ?? undefined"
        :pattern="validation?.pattern ?? undefined"
        :value="modelValue as string"
        :disabled="disabled"
        :aria-invalid="invalid || undefined"
        :class="{ 'setting-input--invalid': invalid }"
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
      class="setting-error"
      role="alert"
      :data-testid="`setting-error-${setting.key}`"
    >{{ error }}</p>

    <div class="setting-row-meta">
      <span
        v-if="setting.restart_required"
        class="setting-badge setting-badge--restart"
        title="This setting takes effect after a restart"
        :data-testid="`restart-badge-${setting.key}`"
      >Requires restart<span class="sr-only"> to take effect</span></span>
      <span
        v-if="setting.db_overridden"
        class="setting-badge setting-badge--overridden"
        :data-testid="`overridden-badge-${setting.key}`"
      >Overridden<span class="sr-only"> — differs from the built-in default</span></span>
      <button
        v-if="setting.db_overridden"
        type="button"
        class="btn btn-secondary btn-small"
        :aria-label="`Reset ${setting.label} to default`"
        :disabled="disabled || resetting"
        :data-testid="`reset-${setting.key}`"
        @click="emit('reset')"
      >{{ resetting ? 'Resetting…' : 'Reset to default' }}</button>
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

.setting-select {
  align-self: flex-start;
}

.setting-control input[type='text'],
.setting-control input[type='number'] {
  align-self: flex-start;
  min-width: 14rem;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.setting-control input[type='text']:hover,
.setting-control input[type='number']:hover {
  border-color: var(--accent);
}

.setting-control input[type='text']:focus-visible,
.setting-control input[type='number']:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
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

.setting-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px var(--space-2);
  border-radius: 999px;
  /* --text-primary on the tinted background keeps the pill legible; the literal
     word carries the meaning, not the colour, so it never relies on the tint. */
  color: var(--text-primary);
  font-weight: 500;
}

.setting-badge--restart {
  background: color-mix(in srgb, var(--color-warning) 25%, transparent);
}

.setting-badge--overridden {
  background: color-mix(in srgb, var(--accent) 25%, transparent);
}

.setting-input--invalid {
  border-color: var(--color-error) !important;
}

.setting-error {
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 20%, transparent);
  border-left: 3px solid var(--color-error);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  margin: 0;
}
</style>
