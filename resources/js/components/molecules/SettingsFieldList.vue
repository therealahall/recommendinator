<script setup lang="ts">
import { computed } from 'vue'
import SettingControl from '@/components/molecules/SettingControl.vue'
import SettingSecret from '@/components/molecules/SettingSecret.vue'
import type { SettingBufferValue } from '@/composables/useSettingsBuffer'
import type { SettingView, SettingViewSecret, SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    settings: SettingView[]
    values: Record<string, SettingBufferValue>
    disabled?: boolean
    errors?: Record<string, string>
    resetting?: Record<string, boolean>
    secretBusy?: Record<string, boolean>
  }>(),
  {
    disabled: false,
    errors: () => ({}),
    resetting: () => ({}),
    secretBusy: () => ({}),
  },
)

const emit = defineEmits<{
  update: [key: string, value: SettingBufferValue]
  reset: [key: string]
  'set-secret': [key: string, value: string]
  'clear-secret': [key: string]
}>()

const controls = computed(() =>
  props.settings.filter((setting): setting is SettingViewValue => !setting.sensitive),
)
const secrets = computed(() =>
  props.settings.filter((setting): setting is SettingViewSecret => setting.sensitive),
)
</script>

<template>
  <div class="settings-field-list">
    <SettingControl
      v-for="setting in controls"
      :key="setting.key"
      :setting="setting"
      :model-value="values[setting.key] ?? null"
      :disabled="disabled"
      :error="errors[setting.key] ?? ''"
      :resetting="resetting[setting.key] ?? false"
      @update:model-value="emit('update', setting.key, $event)"
      @reset="emit('reset', setting.key)"
    />

    <fieldset v-if="secrets.length > 0" class="source-form-secrets">
      <legend>Secrets</legend>
      <SettingSecret
        v-for="setting in secrets"
        :key="setting.key"
        :setting="setting"
        :verbs-locked="disabled"
        :busy="secretBusy[setting.key] ?? false"
        @set="emit('set-secret', setting.key, $event)"
        @clear="emit('clear-secret', setting.key)"
      />
    </fieldset>
  </div>
</template>

<style scoped>
/* .source-form-secrets is a shared primitive in base.css; only the spacing that
   separates the fieldset from the controls above it belongs here. */
.source-form-secrets {
  margin: var(--space-3) 0;
}
</style>
