<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import SourceConnectPanel from '@/components/organisms/SourceConnectPanel.vue'
import SourceScheduleSelect from '@/components/organisms/SourceScheduleSelect.vue'
import SourceSettingsPanel from '@/components/organisms/SourceSettingsPanel.vue'
import SourceSyncOutcome from '@/components/molecules/SourceSyncOutcome.vue'
import SourceSyncProgress from '@/components/molecules/SourceSyncProgress.vue'
import SourceSyncSchedule from '@/components/molecules/SourceSyncSchedule.vue'
import { useDataStore } from '@/stores/data'
import { rescueFocus } from '@/utils/focus'
import type { SyncJobResponse, SyncSourceResponse } from '@/types/api'

const props = defineProps<{
  source: SyncSourceResponse
  syncing: boolean
  job?: SyncJobResponse | null
}>()

const emit = defineEmits<{
  sync: [sourceId: string]
}>()

const data = useDataStore()
const expanded = ref(false)
const detailsLoaded = ref(false)
const detailsLoading = ref(false)
const detailsError = ref('')
const detailsMessage = ref('')
const migrating = ref(false)
const migrateError = ref('')
const gateRevision = ref(0)

// Accordion.vue hides its panel rather than unmounting it, so there is nothing
// in this tree to scope to. The testids are source-scoped, which is the guard.
function panelControl(testid: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)
}

const schema = computed(() => data.sourceSchemas[props.source.id])
const config = computed(() => data.sourceConfigs[props.source.id])
const isMigrated = computed(() => config.value?.migrated === true)

// Without the catch a rejection left detailsLoaded and detailsLoading both
// false, which matches neither template branch: the panel opened onto nothing
// at all, and re-expanding failed the same silent way.
async function ensureDetails(): Promise<void> {
  if (detailsLoaded.value || detailsLoading.value) return
  detailsLoading.value = true
  detailsError.value = ''
  try {
    await Promise.all([
      data.loadSourceSchema(props.source.id),
      data.loadSourceConfig(props.source.id),
    ])
    detailsLoaded.value = true
  } catch (err) {
    detailsError.value = err instanceof Error ? err.message : 'Unknown error'
  } finally {
    detailsLoading.value = false
  }
}

async function onRetryDetails(): Promise<void> {
  if (detailsLoading.value) return
  detailsMessage.value = 'Loading these settings again…'
  await ensureDetails()
  if (detailsError.value) {
    // A second failure changes nothing else on screen, and the Retry unmounts
    // for the length of the request, taking the focus with it.
    detailsMessage.value = 'Still could not load these settings. Try again in a moment.'
    await nextTick()
    rescueFocus(panelControl(`details-retry-${props.source.id}`))
    return
  }
  detailsMessage.value = ''
  await nextTick()
  // Retry unmounts with the error it belonged to, so the keyboard follows the
  // settings it just loaded rather than dropping to <body> (WCAG 2.4.3).
  rescueFocus(panelControl(`details-body-${props.source.id}`))
}

async function onToggleExpanded(value: boolean): Promise<void> {
  expanded.value = value
  if (value) await ensureDetails()
}

function onSyncClick(event: MouseEvent): void {
  event.stopPropagation()
  if (props.syncing) return
  emit('sync', props.source.id)
}

async function onMigrate(): Promise<void> {
  if (migrating.value) return
  migrating.value = true
  migrateError.value = ''
  try {
    await data.migrateSource(props.source.id)
  } catch (err) {
    migrateError.value = err instanceof Error ? err.message : 'Unknown error'
    await nextTick()
    rescueFocus(panelControl(`migrate-error-${props.source.id}`))
  } finally {
    migrating.value = false
  }
}

// A disabled source never runs, and the accessible name below says so: letting
// the visible label read "Syncing…" would leave the two disagreeing (WCAG 2.5.3).
const syncLabel = computed(() =>
  props.syncing && props.source.enabled ? 'Syncing…' : 'Sync',
)

const runsLoading = ref(false)
const runsError = ref('')
const runs = computed(() => data.sourceRuns[props.source.id] ?? [])

async function onOpenRunHistory(): Promise<void> {
  if (runsLoading.value) return
  runsLoading.value = true
  runsError.value = ''
  try {
    await data.loadSourceRuns(props.source.id)
  } catch (err) {
    runsError.value = err instanceof Error ? err.message : 'Unknown error'
  } finally {
    runsLoading.value = false
  }
}

const intervalOptions = computed(() => schema.value?.sync_intervals ?? [])
// Only the schema carries the labels, so a collapsed row reads the raw key.
const intervalLabel = computed(
  () =>
    intervalOptions.value.find((option) => option.key === props.source.sync_interval)
      ?.label ?? props.source.sync_interval,
)
</script>

<template>
  <Accordion
    :id="source.id"
    :expanded="expanded"
    :class="{ 'source-accordion--disabled': !props.source.enabled }"
    @update:expanded="onToggleExpanded"
  >
    <template #header>
      <span class="source-accordion-header-text">
        <span class="source-accordion-name">{{ source.display_name }}</span>
        <span v-if="!props.source.enabled" class="badge">Disabled</span>
      </span>
    </template>

    <template #header-actions>
      <button
        type="button"
        class="btn btn-primary sync-btn"
        :data-testid="`sync-btn-${source.id}`"
        :disabled="!props.source.enabled"
        :aria-disabled="props.syncing || undefined"
        :aria-label="
          !props.source.enabled
            ? `Sync ${source.display_name} — source is disabled`
            : props.syncing
            ? `Syncing ${source.display_name} — in progress`
            : `Sync ${source.display_name}`
        "
        @click="onSyncClick"
      >{{ syncLabel }}</button>
    </template>

    <template #notice>
      <SourceSyncSchedule
        :source-id="source.id"
        :source-name="source.display_name"
        :interval-label="intervalLabel"
        :last-run-at="source.last_run_at"
        :last-run-status="source.last_run_status"
        :next-run-at="source.next_run_at"
        :runs="runs"
        :loading="runsLoading"
        :error="runsError"
        @open="onOpenRunHistory"
      />
      <SourceSyncProgress :source-name="source.display_name" :job="job" />
      <!-- Outside the accordion header, whose slot is the trigger button's
           content: this would otherwise run into the source name in its
           accessible name. -->
      <SourceSyncOutcome
        :source-id="source.id"
        :source-name="source.display_name"
        :syncing="syncing"
        :job="job"
      />
    </template>

    <div v-if="detailsLoading && !detailsLoaded" class="state state--loading">
      <span class="spinner" /> Loading…
    </div>

    <!--
      Plain content, not role="alert": it can only appear as the body first
      renders, where an alert arrives already populated and is read as page
      content.
    -->
    <div v-else-if="detailsError" class="state state--error">
      <p :data-testid="`details-error-${source.id}`">
        Could not load these settings: {{ detailsError }}
      </p>
      <button
        type="button"
        class="btn btn-secondary"
        :data-testid="`details-retry-${source.id}`"
        :aria-label="`Retry loading the settings for ${source.display_name}`"
        :aria-disabled="detailsLoading || undefined"
        @click="onRetryDetails"
      >{{ detailsLoading ? 'Retrying…' : 'Retry' }}</button>
    </div>

    <div
      v-else-if="config && schema"
      :data-testid="`details-body-${source.id}`"
      tabindex="-1"
      class="focus-fallback"
    >
      <template v-if="!isMigrated">
        <p class="source-accordion-explainer">
          This source is configured via <code>config.yaml</code>. Migrate it to the
          database to edit its settings here.
        </p>
        <button
          type="button"
          class="btn btn-primary"
          :data-testid="`migrate-btn-${source.id}`"
          :aria-disabled="migrating || undefined"
          @click="onMigrate"
        >{{ migrating ? 'Migrating…' : 'Migrate to DB' }}</button>
        <p
          class="state state--error source-accordion-error focus-fallback"
          :data-testid="`migrate-error-${source.id}`"
          role="alert"
          tabindex="-1"
        >{{ migrateError }}</p>
      </template>

      <template v-else>
        <SourceConnectPanel
          :source-id="source.id"
          :source-name="source.display_name"
          :plugin="config.plugin"
          :source-enabled="config.enabled"
          :disabled="props.syncing"
          :expanded="expanded"
          :gate-revision="gateRevision"
        />

        <SourceScheduleSelect
          :source-id="source.id"
          :source-name="source.display_name"
          :interval="source.sync_interval"
          :options="intervalOptions"
          :expanded="expanded"
        />

        <SourceSettingsPanel
          :source="source"
          :fields="schema.fields"
          :config="config"
          :verbs-locked="props.syncing"
          @gate-changed="gateRevision += 1"
        />
      </template>
    </div>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p
      class="source-accordion-details-message"
      :data-testid="`details-message-${source.id}`"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ detailsMessage }}</p>
  </Accordion>
</template>

<style scoped>
.source-accordion-header-text {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.sync-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: var(--border-default);
  color: var(--text-secondary);
  border-color: var(--border-default);
  pointer-events: none;
}

.sync-btn:disabled:hover {
  background: var(--border-default);
}

.source-accordion-name {
  font-weight: var(--weight-semibold);
}

/* Secondary text on the muted parts, and nothing softer than that: opacity
   composites against the surface, and a softer border would leave the panel
   without the 3:1 edge that says where it is (WCAG 1.4.3, 1.4.11). */
.source-accordion--disabled :deep(.accordion-trigger) {
  color: var(--text-secondary);
}

.source-accordion-explainer {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin-bottom: var(--space-3);
}

.source-accordion-error:not(:empty) {
  margin-top: var(--space-3);
}

.state--error p {
  margin: 0;
}

.source-accordion-details-message {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.source-accordion-details-message:not(:empty) {
  margin-top: var(--space-3);
}

</style>
