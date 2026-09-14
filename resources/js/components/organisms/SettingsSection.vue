<script lang="ts">
// Module scope, not <script setup>: setup runs per component instance, and the
// Settings page renders one section per registry section, so a constant map
// declared there would be rebuilt for each.
const CAUTION_BY_SECTION: Record<string, string> = {
  web: 'this setting controls who can reach this instance. Widening the allowed CORS origins lets other sites in your browser read and modify your data.',
  logging: 'these settings control how much this instance records and where it writes it. Verbose levels can capture sensitive request detail on disk.',
}
</script>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import AppIcon from '@/components/atoms/AppIcon.vue'
import SettingsFieldList from '@/components/molecules/SettingsFieldList.vue'
import EnrichmentProviders from '@/components/organisms/EnrichmentProviders.vue'
import { useAnnouncer } from '@/composables/useAnnouncer'
import { useSectionSave } from '@/composables/useSectionSave'
import { useSettingsBuffer, type SettingBufferValue } from '@/composables/useSettingsBuffer'
import { useSettingsStore } from '@/stores/settings'
import { rescueFocus } from '@/utils/focus'
import { humanizeSection } from '@/utils/format'
import { groupSettings, providerGroups } from '@/utils/settingsGroups'
import type { SettingsSection, SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    section: SettingsSection
    initiallyExpanded?: boolean
  }>(),
  { initiallyExpanded: false },
)

const emit = defineEmits<{ 'update:dirty': [dirty: boolean] }>()

const store = useSettingsStore()

const valueSettings = computed(() =>
  props.section.settings.filter((setting): setting is SettingViewValue => !setting.sensitive),
)
// Providers leave first, then the order they rank: what stays is what a generic
// control renders, and the two together cover every key in the section.
const split = computed(() => providerGroups(props.section.settings))
const orderSetting = computed(
  () =>
    split.value.rest.find(
      (setting): setting is SettingViewValue =>
        !setting.sensitive && setting.widget === 'provider-order',
    ) ?? null,
)
const plain = computed(() =>
  split.value.rest.filter((setting) => setting !== orderSetting.value),
)

// The two halves partition the section: a setting the registry marks advanced
// is never also offered above, and none can fall out of both and go unrendered.
const advanced = computed(() => plain.value.filter((setting) => setting.advanced))
const grouped = computed(() =>
  groupSettings(plain.value.filter((setting) => !setting.advanced)),
)
// Advanced settings sit last in the panel: the caution note above covers them,
// so a disclosure of their own would only add a click.
const panelSettings = computed(() => [...grouped.value.ungrouped, ...advanced.value])

const sectionKey = computed(() => props.section.section)
const title = computed(() => humanizeSection(sectionKey.value))

const expanded = ref(props.initiallyExpanded)
const expandedGroups = reactive<Record<string, boolean>>({})

const edits = useSettingsBuffer(() => valueSettings.value)
const { buffer, dirty } = edits

const providerOrder = computed(() => {
  const key = orderSetting.value?.key
  const value = key === undefined ? undefined : buffer[key]
  return Array.isArray(value) ? value : []
})

const secretDrafts = reactive<Record<string, boolean>>({})
const unsaved = computed(() => dirty.value || Object.values(secretDrafts).some(Boolean))

watch(unsaved, (value) => emit('update:dirty', value))
const { message: actionMessage, announce, report } = useAnnouncer()

const { saving, saveStatus, saveErrorText, save } = useSectionSave(
  () => sectionKey.value,
  () => valueSettings.value,
  edits,
  announce,
)

const cautionText = computed(
  () => CAUTION_BY_SECTION[sectionKey.value] ?? 'these settings change how this instance runs.',
)

const resetting = reactive<Record<string, boolean>>({})
const secretBusy = reactive<Record<string, boolean>>({})

const saveButton = ref<HTMLButtonElement | null>(null)
const saveLockId = computed(() => `save-lock-${sectionKey.value}`)

// All four verbs refresh the section, and a claimed key goes to whichever view
// lands first. Read off `write`, not the claims: a secret claims no key, and an
// enumerated lock has twice been short a verb.
const locked = edits.writing

// A refused value inside a collapsed accordion is an error nobody can see.
function reveal(setting: SettingViewValue): void {
  expanded.value = true
  const group = [...grouped.value.groups, ...split.value.providers].find((entry) =>
    entry.settings.some((member) => member.key === setting.key),
  )
  if (group) expandedGroups[group.id] = true
}

async function onSave(): Promise<void> {
  if (locked.value) return
  const refused = await save()
  if (!refused) {
    // A pointer operator who never tabbed out of a field had it disabled under
    // them for the request, dropping focus to the body (WCAG 2.4.3).
    await nextTick()
    rescueFocus(saveButton.value)
    return
  }
  // Focus follows the disclosure, so keyboard and AT users land on the field
  // that was rejected rather than on the panel that opened.
  reveal(refused)
  await nextTick()
  // Save catches it for the order, which the provider list renders rather than a
  // control, so its key resolves to no field (WCAG 2.4.3).
  ;(document.getElementById(`setting-${refused.key}`) ?? saveButton.value)?.focus()
}

async function onReset(key: string): Promise<void> {
  resetting[key] = true
  const reset = () => edits.write([key], () => store.resetSetting(key))
  await report(reset, 'Reset to default.', 'Reset failed.')
  resetting[key] = false
  // Only a landed reset strands anyone: its button unmounts with the override it
  // removed, and a setting rendered outside a control — the order — has no field
  // to catch the focus (WCAG 2.4.3).
  await nextTick()
  rescueFocus(document.getElementById(`setting-${key}`) ?? saveButton.value)
}

async function onSecret(
  key: string,
  action: () => Promise<void>,
  done: string,
  failed: string,
): Promise<void> {
  secretBusy[key] = true
  // No keys: a secret is write-only and never in the buffer. It refreshes the
  // section all the same, so it locks the panel like any other write.
  await report(() => edits.write([], action), done, failed)
  secretBusy[key] = false
}

function onSetSecret(key: string, value: string): Promise<void> {
  const failed = 'Saving the secret failed.'
  return onSecret(key, () => store.setSecret(key, value), 'Secret saved.', failed)
}

function onClearSecret(key: string): Promise<void> {
  const failed = 'Clearing the secret failed.'
  return onSecret(key, () => store.clearSecret(key), 'Secret cleared.', failed)
}

function onUpdate(key: string, value: SettingBufferValue): void {
  buffer[key] = value
}

// A buffer edit like any other, so a reorder leaves with the section save
// rather than racing it through a write of its own.
function onOrder(order: string[]): void {
  const key = orderSetting.value?.key
  if (key !== undefined) buffer[key] = order
}

function onExpand(id: string, value: boolean): void {
  expandedGroups[id] = value
}

function onSecretDraft(key: string, hasDraft: boolean): void {
  secretDrafts[key] = hasDraft
}
</script>

<template>
  <div class="settings-section">
    <Accordion
      :id="`section-${sectionKey}`"
      :heading-level="3"
      :expanded="expanded"
      @update:expanded="expanded = $event"
    >
      <template #header>
        <span class="settings-section-header">
          <span class="settings-section-name">{{ title }}</span>
          <span class="settings-section-count">
            {{ section.settings.length }} setting{{ section.settings.length === 1 ? '' : 's' }}
          </span>
          <span
            v-if="unsaved"
            class="badge"
            data-tone="warning"
            :data-testid="`dirty-${sectionKey}`"
          >Unsaved changes</span>
        </span>
      </template>

      <p v-if="advanced.length > 0" class="settings-caution" role="note">
        <strong>Caution:</strong> {{ cautionText }} Change these only if you
        understand the impact.
      </p>
      <SettingsFieldList
        v-if="panelSettings.length > 0"
        :settings="panelSettings"
        :values="buffer"
        :disabled="locked"
        :errors="store.fieldErrors"
        :resetting="resetting"
        :secret-busy="secretBusy"
        @update="onUpdate"
        @reset="onReset"
        @set-secret="onSetSecret"
        @clear-secret="onClearSecret"
        @secret-draft="onSecretDraft"
      />

      <EnrichmentProviders
        v-if="split.providers.length > 0"
        :providers="split.providers"
        :order="providerOrder"
        :order-setting="orderSetting"
        :values="buffer"
        :disabled="locked"
        :errors="store.fieldErrors"
        :resetting="resetting"
        :secret-busy="secretBusy"
        :expanded="expandedGroups"
        @update="onUpdate"
        @update:order="onOrder"
        @expand="onExpand"
        @reset="onReset"
        @set-secret="onSetSecret"
        @clear-secret="onClearSecret"
        @secret-draft="onSecretDraft"
        @announce="announce"
      />

      <Accordion
        v-for="group in grouped.groups"
        :id="`group-${group.id}`"
        :key="group.id"
        :heading-level="4"
        :expanded="expandedGroups[group.id] ?? false"
        class="settings-subgroup"
        @update:expanded="expandedGroups[group.id] = $event"
      >
        <template #header>
          {{ group.label }} · {{ group.settings.length }} settings
        </template>
        <SettingsFieldList
          :settings="group.settings"
          :values="buffer"
          :disabled="locked"
          :errors="store.fieldErrors"
          :resetting="resetting"
          :secret-busy="secretBusy"
          @update="onUpdate"
          @reset="onReset"
          @set-secret="onSetSecret"
          @clear-secret="onClearSecret"
          @secret-draft="onSecretDraft"
        />
      </Accordion>

      <div v-if="valueSettings.length > 0" class="settings-section-actions">
        <!-- Deliberately NOT a live region: the error span below is one already,
             and aria-atomic here drags the button's own label into every
             announcement. The saved pill is visible text; the region at the foot
             of the section speaks for it. -->
        <div class="settings-section-save-group">
          <span
            v-if="saveStatus === 'saved'"
            class="badge"
            data-tone="success"
            :data-testid="`save-status-${sectionKey}`"
          >Saved<AppIcon name="check" /></span>
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
            ref="saveButton"
            type="button"
            class="btn btn-primary"
            :data-testid="`save-${sectionKey}`"
            :aria-disabled="locked || undefined"
            :aria-describedby="locked && !saving ? saveLockId : undefined"
            @click="onSave"
          >{{ saving ? 'Saving…' : 'Save' }}<span class="sr-only"> {{ title }}</span></button>
          <span v-if="locked && !saving" :id="saveLockId" class="sr-only"
            >Unavailable while this section has a change in flight.</span
          >
        </div>
      </div>
    </Accordion>

    <!-- Outside the accordion: a collapsed panel is `hidden`, which takes the
         region out of the accessibility tree and silences every announcement. -->
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ actionMessage }}</p>
  </div>
</template>

<style scoped>
.settings-section {
  margin-bottom: var(--space-4);
}

.settings-section-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

/* How much is behind a collapsed row, so the page can be scanned shut. */
.settings-section-count {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
}

/* Flat: a nested card carrying the same elevation as the section around it
   reads as a sibling of that section rather than as its contents. The border
   stays --border-default, which is the 3:1 edge that identifies an accordion. */
.settings-subgroup {
  margin-top: var(--space-3);
  box-shadow: var(--elevation-0);
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
