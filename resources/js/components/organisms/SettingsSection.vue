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
import { computed, nextTick, reactive, ref } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import SettingsFieldList from '@/components/molecules/SettingsFieldList.vue'
import { useAnnouncer } from '@/composables/useAnnouncer'
import { useSectionSave } from '@/composables/useSectionSave'
import { useSettingsBuffer, type SettingBufferValue } from '@/composables/useSettingsBuffer'
import { useSettingsStore } from '@/stores/settings'
import { rescueFocus } from '@/utils/focus'
import { humanizeSection } from '@/utils/format'
import { groupSettings } from '@/utils/settingsGroups'
import type { SettingsSection, SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    section: SettingsSection
    initiallyExpanded?: boolean
  }>(),
  { initiallyExpanded: false },
)

const store = useSettingsStore()

const valueSettings = computed(() =>
  props.section.settings.filter((setting): setting is SettingViewValue => !setting.sensitive),
)
// The two halves partition the section: a setting the registry marks advanced
// is never also offered above, and none can fall out of both and go unrendered.
const advanced = computed(() => props.section.settings.filter((setting) => setting.advanced))
const grouped = computed(() =>
  groupSettings(props.section.settings.filter((setting) => !setting.advanced)),
)
// The Advanced disclosure hides advanced settings among ordinary ones. A section
// that is nothing but advanced has none to hide them from, so the disclosure is
// pure cost: its settings render in the section panel like any others.
const nestAdvanced = computed(
  () =>
    advanced.value.length > 0 &&
    (grouped.value.ungrouped.length > 0 || grouped.value.groups.length > 0),
)
const panelSettings = computed(() =>
  nestAdvanced.value ? grouped.value.ungrouped : [...grouped.value.ungrouped, ...advanced.value],
)

const sectionKey = computed(() => props.section.section)
const title = computed(() => humanizeSection(sectionKey.value))

const expanded = ref(props.initiallyExpanded)
const advancedExpanded = ref(false)
const expandedGroups = reactive<Record<string, boolean>>({})

const { buffer, changedUpdates } = useSettingsBuffer(() => valueSettings.value)
const { message: actionMessage, announce, report } = useAnnouncer()

const { saving, saveStatus, saveErrorText, save } = useSectionSave(
  () => sectionKey.value,
  () => valueSettings.value,
  changedUpdates,
  announce,
)

const cautionText = computed(
  () => CAUTION_BY_SECTION[sectionKey.value] ?? 'these settings change how this instance runs.',
)

const resetting = reactive<Record<string, boolean>>({})
const secretBusy = reactive<Record<string, boolean>>({})

// A refused value inside a collapsed accordion is an error nobody can see.
function reveal(setting: SettingViewValue): void {
  expanded.value = true
  if (setting.advanced) advancedExpanded.value = true
  const group = grouped.value.groups.find((entry) =>
    entry.settings.some((member) => member.key === setting.key),
  )
  if (group) expandedGroups[group.id] = true
}

async function onSave(): Promise<void> {
  const refused = await save()
  if (!refused) return
  // Focus follows the disclosure, so keyboard and AT users land on the field
  // that was rejected rather than on the panel that opened.
  reveal(refused)
  await nextTick()
  document.getElementById(`setting-${refused.key}`)?.focus()
}

async function onReset(key: string): Promise<void> {
  resetting[key] = true
  await report(() => store.resetSetting(key), 'Reset to default.', 'Reset failed.')
  resetting[key] = false
  // Only a landed reset strands anyone: its button unmounts with the override it
  // removed. A refused one is aria-disabled, so it keeps both its place and the
  // operator's focus, and the seam declines (WCAG 2.4.3).
  await nextTick()
  rescueFocus(document.getElementById(`setting-${key}`))
}

async function onSecret(
  key: string,
  action: () => Promise<void>,
  done: string,
  failed: string,
): Promise<void> {
  secretBusy[key] = true
  await report(action, done, failed)
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
        </span>
      </template>

      <p v-if="!nestAdvanced && advanced.length > 0" class="settings-caution" role="note">
        <strong>Caution:</strong> {{ cautionText }} Change these only if you
        understand the impact.
      </p>
      <SettingsFieldList
        v-if="panelSettings.length > 0"
        :settings="panelSettings"
        :values="buffer"
        :disabled="saving"
        :errors="store.fieldErrors"
        :resetting="resetting"
        :secret-busy="secretBusy"
        @update="onUpdate"
        @reset="onReset"
        @set-secret="onSetSecret"
        @clear-secret="onClearSecret"
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
          {{ group.label }} · {{ group.settings.length }} setting{{
            group.settings.length === 1 ? '' : 's'
          }}
        </template>
        <SettingsFieldList
          :settings="group.settings"
          :values="buffer"
          :disabled="saving"
          :errors="store.fieldErrors"
          :resetting="resetting"
          :secret-busy="secretBusy"
          @update="onUpdate"
          @reset="onReset"
          @set-secret="onSetSecret"
          @clear-secret="onClearSecret"
        />
      </Accordion>

      <Accordion
        v-if="nestAdvanced"
        :id="`adv-${sectionKey}`"
        :heading-level="4"
        :expanded="advancedExpanded"
        class="settings-subgroup"
        @update:expanded="advancedExpanded = $event"
      >
        <template #header>Advanced · {{ advanced.length }} setting{{ advanced.length === 1 ? '' : 's' }}</template>
        <p class="settings-caution" role="note">
          <strong>Caution:</strong> {{ cautionText }} Change these only if you
          understand the impact.
        </p>
        <SettingsFieldList
          :settings="advanced"
          :values="buffer"
          :disabled="saving"
          :errors="store.fieldErrors"
          :resetting="resetting"
          :secret-busy="secretBusy"
          @update="onUpdate"
          @reset="onReset"
          @set-secret="onSetSecret"
          @clear-secret="onClearSecret"
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
               useSectionSave guards re-entry instead. -->
          <button
            type="button"
            class="btn btn-primary"
            :data-testid="`save-${sectionKey}`"
            :aria-disabled="saving || undefined"
            @click="onSave"
          >{{ saving ? 'Saving…' : `Save ${title}` }}</button>
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

.settings-section-name {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
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
