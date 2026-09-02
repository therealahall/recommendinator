<script lang="ts">
// Module scope, not <script setup>: setup runs per component instance, and the
// Settings page renders one section card per registry section, so a constant map
// declared there would be rebuilt for each.
const CAUTION_BY_SECTION: Record<string, string> = {
  web: 'this setting controls who can reach this instance. Widening the allowed CORS origins lets other sites in your browser read and modify your data.',
  logging: 'these settings control how much this instance records and where it writes it. Verbose levels can capture sensitive request detail on disk.',
}
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import SettingControl from '@/components/molecules/SettingControl.vue'
import SettingSecret from '@/components/molecules/SettingSecret.vue'
import { useSettingsStore } from '@/stores/settings'
import { rescueFocus } from '@/utils/focus'
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

// The service's pattern message is deictic ("see this setting's help"), and the
// banner sits in the section footer naming no field. Prefix the offending label
// so the pointer has a referent from here too.
const saveErrorText = computed(() => {
  const message = saveError.value || 'failed to save'
  const offending = valueSettings.value.find(
    (setting) => store.fieldErrors[setting.key],
  )
  return offending ? `${offending.label}: ${message}` : message
})

const cautionText = computed(
  () =>
    CAUTION_BY_SECTION[sectionKey.value] ??
    'these settings change how this instance runs.',
)

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
// would be dropped.
async function announce(message: string): Promise<void> {
  // Blank the region first, then set the message, forcing a mutation the AT
  // re-announces every time.
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
  // Guard re-entry here so the button can stay focusable (aria-disabled does not
  // block activation), which keeps focus where the user left it on the success
  // path (WCAG 2.4.3).
  if (saving.value) return
  clearSaveTimer()
  const updates = changedUpdates()
  // Nothing edited: don't PUT an empty object and then claim "Saved ✓", which
  // tells the user a write happened that did not.
  if (Object.keys(updates).length === 0) {
    await announce('No changes to save.')
    return
  }
  const ok = await store.saveSection(sectionKey.value, updates)
  if (ok) {
    // Focus stays on Save, whose label reverts to what it was: without this the
    // write lands in silence.
    await announce(`${title.value} saved.`)
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

function failureText(error: unknown, fallback: string): string {
  return error instanceof Error ? `${fallback} ${error.message}` : fallback
}

async function onReset(key: string): Promise<void> {
  resetting[key] = true
  try {
    await store.resetSetting(key)
    await announce('Reset to default.')
  } catch (error) {
    await announce(failureText(error, 'Reset failed.'))
  } finally {
    resetting[key] = false
  }
  // Only a landed reset strands anyone: its button unmounts with the override it
  // removed. A refused one is aria-disabled, so it keeps both its place and the
  // operator's focus, and the seam declines (WCAG 2.4.3).
  await nextTick()
  rescueFocus(document.getElementById(`setting-${key}`))
}

async function onSetSecret(key: string, value: string): Promise<void> {
  secretBusy[key] = true
  try {
    await store.setSecret(key, value)
    await announce('Secret saved.')
  } catch (error) {
    await announce(failureText(error, 'Saving the secret failed.'))
  } finally {
    secretBusy[key] = false
  }
}

async function onClearSecret(key: string): Promise<void> {
  secretBusy[key] = true
  try {
    await store.clearSecret(key)
    await announce('Secret cleared.')
  } catch (error) {
    await announce(failureText(error, 'Clearing the secret failed.'))
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
          :verbs-locked="saving"
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
          <strong>Caution:</strong> {{ cautionText }} Change these only if you
          understand the impact.
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
      <!-- Deliberately NOT a live region: the error span below is one already,
           and aria-atomic here drags the button's own label into every
           announcement. The saved pill is visible text; the region at the foot
           of the card speaks for it. -->
      <div class="settings-section-save-group">
        <span
          v-if="saveStatus === 'saved'"
          class="badge"
          data-tone="success"
          :data-testid="`save-status-${sectionKey}`"
        >Saved ✓</span>
        <span
          v-else-if="saveStatus === 'error'"
          class="badge badge--wrap"
          data-tone="error"
          :data-testid="`save-status-${sectionKey}`"
          role="alert"
        >Error: {{ saveErrorText }}</span>
        <!-- aria-disabled, not disabled: disabling the button the user just
             activated blurs it and drops focus to <body> for the whole save.
             onSave guards re-entry instead. -->
        <button
          type="button"
          class="btn btn-primary"
          :data-testid="`save-${sectionKey}`"
          :aria-disabled="saving || undefined"
          @click="onSave"
        >{{ saving ? 'Saving…' : `Save ${title}` }}</button>
      </div>
    </div>

    <!-- Persistent live region: every save, reset and secret outcome lands here. -->
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

</style>
