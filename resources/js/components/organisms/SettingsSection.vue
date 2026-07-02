<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import SettingControl from '@/components/molecules/SettingControl.vue'
import SettingSecret from '@/components/molecules/SettingSecret.vue'
import { useSettingsStore } from '@/stores/settings'
import { humanizeSection } from '@/utils/format'
import type {
  SettingsSection,
  SettingView,
  SettingViewSecret,
  SettingViewValue,
} from '@/types/api'

const props = defineProps<{
  section: SettingsSection
}>()

const store = useSettingsStore()

type BufferValue = string | number | boolean | string[]

function isValue(setting: SettingView): setting is SettingViewValue {
  return !setting.sensitive
}
function isSecret(setting: SettingView): setting is SettingViewSecret {
  return setting.sensitive
}

const valueSettings = computed(() => props.section.settings.filter(isValue))
const nonAdvanced = computed(() => valueSettings.value.filter((setting) => !setting.advanced))
const advanced = computed(() => valueSettings.value.filter((setting) => setting.advanced))
const secrets = computed(() => props.section.settings.filter(isSecret))

const title = computed(() => humanizeSection(props.section.section))
const sectionKey = computed(() => props.section.section)
const groupId = computed(() => `settings-group-${sectionKey.value}`)
const headingId = computed(() => `settings-heading-${sectionKey.value}`)

const advancedExpanded = ref(false)

// Local edit buffer + a snapshot of the server value for change detection.
// Server truth lives in the store; the buffer is this section's working copy.
const buffer = reactive<Record<string, BufferValue>>({})
const original = reactive<Record<string, BufferValue>>({})

function coerce(setting: SettingViewValue): BufferValue {
  const rawValue = setting.value
  switch (setting.type) {
    case 'bool':
      return Boolean(rawValue)
    case 'int':
    case 'float': {
      if (typeof rawValue === 'number') return rawValue
      const parsedNumber = Number(rawValue)
      return Number.isFinite(parsedNumber) ? parsedNumber : 0
    }
    case 'list':
      return Array.isArray(rawValue) ? rawValue.map(String) : []
    default:
      return rawValue == null ? '' : String(rawValue)
  }
}

function syncBuffer(): void {
  for (const setting of valueSettings.value) {
    const coerced = coerce(setting)
    buffer[setting.key] = coerced
    original[setting.key] = coerced
  }
}

watch(() => props.section, syncBuffer, { immediate: true, deep: true })

const saving = computed(() => store.saving[sectionKey.value] ?? false)
const saveStatus = computed(() => store.saveStatus[sectionKey.value] ?? 'idle')
const saveError = computed(() => store.saveError[sectionKey.value] ?? '')

const resetting = reactive<Record<string, boolean>>({})
const secretBusy = reactive<Record<string, boolean>>({})
const actionMessage = ref('')

let saveStatusTimer: ReturnType<typeof setTimeout> | null = null

function clearSaveTimer(): void {
  if (saveStatusTimer) {
    clearTimeout(saveStatusTimer)
    saveStatusTimer = null
  }
}

// A persistent role="status" region only fires the screen reader when its text
// actually changes, so a repeated identical action (e.g. two resets in a row)
// would be dropped. Blank the region first, then set the message, forcing a
// mutation the AT re-announces every time.
async function announce(message: string): Promise<void> {
  actionMessage.value = ''
  await nextTick()
  actionMessage.value = message
}

function changedUpdates(): Record<string, unknown> {
  const updates: Record<string, unknown> = {}
  for (const setting of valueSettings.value) {
    const key = setting.key
    if (JSON.stringify(buffer[key]) !== JSON.stringify(original[key])) {
      updates[key] = buffer[key]
    }
  }
  return updates
}

async function onSave(): Promise<void> {
  clearSaveTimer()
  const updates = changedUpdates()
  const ok = await store.saveSection(sectionKey.value, updates)
  if (ok) {
    saveStatusTimer = setTimeout(() => {
      store.clearSaveStatus(sectionKey.value)
      saveStatusTimer = null
    }, 2500)
    return
  }
  // Move focus to the first offending field so keyboard/AT users land on it.
  const offending = valueSettings.value.find((setting) => store.fieldErrors[setting.key])
  if (offending) {
    if (offending.advanced) advancedExpanded.value = true
    await nextTick()
    document.getElementById(`setting-${offending.key}`)?.focus()
  }
}

async function onReset(key: string): Promise<void> {
  resetting[key] = true
  try {
    await store.resetSetting(key)
    await announce('Reset to default.')
    // The Reset button disappears once the override clears, so return focus to
    // the control itself rather than letting it drop to <body> (WCAG 2.4.3).
    await nextTick()
    document.getElementById(`setting-${key}`)?.focus()
  } finally {
    resetting[key] = false
  }
}

async function onSetSecret(key: string, value: string): Promise<void> {
  secretBusy[key] = true
  try {
    await store.setSecret(key, value)
    await announce('Secret saved.')
  } finally {
    secretBusy[key] = false
  }
}

async function onClearSecret(key: string): Promise<void> {
  secretBusy[key] = true
  try {
    await store.clearSecret(key)
    await announce('Secret cleared.')
  } finally {
    secretBusy[key] = false
  }
}

onBeforeUnmount(clearSaveTimer)
</script>

<template>
  <div class="card">
    <h3 :id="headingId">{{ title }}</h3>

    <div :id="groupId" role="group" :aria-labelledby="headingId">
      <SettingControl
        v-for="setting in nonAdvanced"
        :key="setting.key"
        :setting="setting"
        v-model="buffer[setting.key]"
        :disabled="saving"
        :error="store.fieldErrors[setting.key] ?? ''"
        :resetting="resetting[setting.key] ?? false"
        @reset="onReset(setting.key)"
      />

      <fieldset v-if="secrets.length > 0" class="source-form-secrets">
        <legend>Secrets</legend>
        <SettingSecret
          v-for="setting in secrets"
          :key="setting.key"
          :setting="setting"
          :disabled="saving"
          :busy="secretBusy[setting.key] ?? false"
          @set="onSetSecret(setting.key, $event)"
          @clear="onClearSecret(setting.key)"
        />
      </fieldset>

      <Accordion
        v-if="advanced.length > 0"
        :id="`adv-${sectionKey}`"
        :heading-level="4"
        :expanded="advancedExpanded"
        @update:expanded="advancedExpanded = $event"
      >
        <template #header>Advanced · {{ advanced.length }} setting{{ advanced.length === 1 ? '' : 's' }}</template>
        <p class="settings-caution" role="note">
          <strong>Caution:</strong> these settings affect how the server binds and who
          can reach it. Widening CORS allowed origins or changing the bind host can
          expose this instance on your network. Change these only if you understand
          the impact.
        </p>
        <SettingControl
          v-for="setting in advanced"
          :key="setting.key"
          :setting="setting"
          v-model="buffer[setting.key]"
          :disabled="saving"
          :error="store.fieldErrors[setting.key] ?? ''"
          :resetting="resetting[setting.key] ?? false"
          @reset="onReset(setting.key)"
        />
      </Accordion>
    </div>

    <div v-if="valueSettings.length > 0" class="settings-section-actions">
      <div class="settings-section-save-group" aria-live="polite" aria-atomic="true">
        <span
          v-if="saveStatus === 'saved'"
          class="settings-save-status settings-save-status--ok"
          :data-testid="`save-status-${sectionKey}`"
          role="status"
        >Saved ✓</span>
        <span
          v-else-if="saveStatus === 'error'"
          class="settings-save-status settings-save-status--err"
          :data-testid="`save-status-${sectionKey}`"
          role="alert"
        >Error: {{ saveError || 'failed to save' }}</span>
        <button
          type="button"
          class="btn btn-primary"
          :data-testid="`save-${sectionKey}`"
          :disabled="saving"
          @click="onSave"
        >{{ saving ? 'Saving…' : `Save ${title}` }}</button>
      </div>
    </div>

    <!-- Persistent live region so reset/secret confirmations are announced. -->
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ actionMessage }}</p>
  </div>
</template>

<style scoped>
/* Shared .source-form-secrets primitives live in base.css; this section only
   needs the vertical spacing that separates the fieldset from its neighbours. */
.source-form-secrets {
  margin: var(--space-3) 0;
}

.settings-caution {
  font-size: var(--text-sm);
  color: var(--text-primary);
  /* --text-primary on the warning tint keeps the note legible; the leading
     "Caution:" label conveys the meaning without relying on colour. */
  background: color-mix(in srgb, var(--color-warning) 20%, transparent);
  border-left: 3px solid var(--color-warning);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  margin: 0 0 var(--space-3) 0;
}

.settings-section-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-3);
  padding-top: var(--space-4);
  margin-top: var(--space-3);
  border-top: 1px solid var(--border-default);
}

.settings-section-save-group {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.settings-save-status {
  font-size: var(--text-sm);
  font-weight: 500;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  color: var(--text-primary);
}

.settings-save-status--ok {
  background: color-mix(in srgb, var(--color-success) 35%, transparent);
}

.settings-save-status--err {
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
}
</style>
