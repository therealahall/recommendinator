<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import SourceConfigForm from '@/components/molecules/SourceConfigForm.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import TraktDeviceCodeFlow from '@/components/molecules/TraktDeviceCodeFlow.vue'
import { useDataStore } from '@/stores/data'
import type {
  SyncJobResponse,
  SyncSourceProgressResponse,
  SyncSourceResponse,
} from '@/types/api'

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
const migrating = ref(false)
const savingConfig = ref(false)
const togglingEnabled = ref(false)
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const saveStatus = ref<SaveStatus>('idle')
const saveError = ref('')
let saveStatusTimer: ReturnType<typeof setTimeout> | null = null

const schema = computed(() => data.sourceSchemas[props.source.id])
const config = computed(() => data.sourceConfigs[props.source.id])
const isMigrated = computed(() => config.value?.migrated === true)

async function ensureDetails(): Promise<void> {
  if (detailsLoaded.value || detailsLoading.value) return
  detailsLoading.value = true
  try {
    await Promise.all([
      data.loadSourceSchema(props.source.id),
      data.loadSourceConfig(props.source.id),
    ])
    detailsLoaded.value = true
  } finally {
    detailsLoading.value = false
  }
}

async function onToggleExpanded(value: boolean): Promise<void> {
  expanded.value = value
  if (value) await ensureDetails()
}

function onSyncClick(event: MouseEvent): void {
  event.stopPropagation()
  emit('sync', props.source.id)
}

async function onMigrate(): Promise<void> {
  if (migrating.value) return
  migrating.value = true
  try {
    await data.migrateSource(props.source.id)
  } finally {
    migrating.value = false
  }
}

async function onSaveConfig(values: Record<string, unknown>): Promise<void> {
  if (saveStatusTimer) {
    clearTimeout(saveStatusTimer)
    saveStatusTimer = null
  }
  savingConfig.value = true
  saveStatus.value = 'saving'
  saveError.value = ''
  try {
    await data.updateSourceConfig(props.source.id, values)
    saveStatus.value = 'saved'
    saveStatusTimer = setTimeout(() => {
      saveStatus.value = 'idle'
      saveStatusTimer = null
    }, 2500)
  } catch (err) {
    saveStatus.value = 'error'
    saveError.value = err instanceof Error ? err.message : 'Unknown error'
  } finally {
    savingConfig.value = false
  }
}

async function onSetSecret(name: string, value: string): Promise<void> {
  await data.setSourceSecret(props.source.id, name, value)
}

async function onClearSecret(name: string): Promise<void> {
  await data.clearSourceSecret(props.source.id, name)
}

async function onEnabledChange(value: boolean): Promise<void> {
  if (togglingEnabled.value) return
  togglingEnabled.value = true
  try {
    await data.setSourceEnabled(props.source.id, value)
  } finally {
    togglingEnabled.value = false
  }
}

const removing = ref(false)

async function onRemove(): Promise<void> {
  if (removing.value) return
  const ok = window.confirm(
    `Remove "${props.source.display_name}" from the database? This drops ` +
      'every stored secret for this source. The original config.yaml entry ' +
      '(if any) will reappear next reload.',
  )
  if (!ok) return
  removing.value = true
  try {
    await data.deleteSource(props.source.id)
  } finally {
    removing.value = false
  }
}

const isGog = computed(() => props.source.id === 'gog')
const isEpic = computed(() => props.source.id === 'epic_games')
const isTrakt = computed(() => props.source.id === 'trakt')
const showOAuthConnect = computed(() => {
  if (!isMigrated.value) return false
  if (isGog.value) {
    return !data.gogStatus.connected && !!data.gogStatus.authUrl
  }
  if (isEpic.value) {
    return !data.epicStatus.connected && !!data.epicStatus.authUrl
  }
  return false
})
const showOAuthDisconnect = computed(() => {
  if (!isMigrated.value) return false
  if (isGog.value) return data.gogStatus.connected
  if (isEpic.value) return data.epicStatus.connected
  return false
})
const showTraktConnect = computed(
  () => isMigrated.value && isTrakt.value && !data.traktStatus.connected,
)
const showTraktDisconnect = computed(
  () => isMigrated.value && isTrakt.value && data.traktStatus.connected,
)

onBeforeUnmount(() => {
  if (saveStatusTimer) {
    clearTimeout(saveStatusTimer)
    saveStatusTimer = null
  }
})

const syncDisabled = computed(() => props.syncing || !props.source.enabled)
const syncLabel = computed(() => (props.syncing ? 'Syncing…' : 'Sync'))

// Progress for THIS source. When the running job is single-source (the
// user clicked Sync on this row), use the job's top-level counters. When
// the job is the umbrella "All Sources" run, look up this source's slot
// in ``job.sources[]`` by display_name.
const progress = computed<SyncSourceProgressResponse | null>(() => {
  const job = props.job
  if (!job || job.status !== 'running') return null
  if (job.source === props.source.display_name) {
    return {
      source: job.source,
      items_processed: job.items_processed,
      total_items: job.total_items,
      current_item: job.current_item,
      progress_percent: job.progress_percent,
    }
  }
  return (
    job.sources.find((entry) => entry.source === props.source.display_name) ||
    null
  )
})

const progressLabel = computed<string>(() => {
  const entry = progress.value
  if (!entry) return ''
  if (entry.total_items != null && entry.total_items > 0) {
    const pct = entry.progress_percent != null ? ` (${entry.progress_percent}%)` : ''
    return `${entry.items_processed}/${entry.total_items}${pct}`
  }
  return `${entry.items_processed} items`
})

const errorBadgeText = computed<string>(() => {
  const count = props.job?.error_count ?? 0
  return `${count} error${count === 1 ? '' : 's'}`
})

const errorBadgeAriaLabel = computed<string>(
  () => `${errorBadgeText.value} on last sync`,
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
        <span
          v-if="!props.source.enabled"
          class="source-accordion-status-badge"
        >Disabled</span>
        <!--
          v-show (not v-if) keeps the live region in the DOM so JAWS/NVDA
          announce progress as values change rather than treating each
          poll as a fresh insertion (WCAG 4.1.3 status messages).
          All `progress?` derefs are null-safe so the children evaluate
          cleanly while the region is hidden.
        -->
        <span
          v-show="progress"
          class="source-accordion-progress"
          aria-live="polite"
        >
          <span
            v-if="progress?.progress_percent != null"
            class="source-accordion-progress-bar"
            role="progressbar"
            :aria-valuenow="progress.progress_percent"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="`${source.display_name} sync progress: ${progress.progress_percent}%`"
          >
            <span
              class="source-accordion-progress-fill"
              :style="{ width: `${Math.min(100, progress.progress_percent)}%` }"
            />
          </span>
          <span class="source-accordion-progress-counts">{{ progressLabel }}</span>
          <span
            v-if="progress?.current_item"
            class="source-accordion-progress-item"
          >{{ progress.current_item }}</span>
        </span>
        <span
          v-if="!progress && job && job.error_count > 0 && !syncing"
          class="source-accordion-error-badge"
          :aria-label="errorBadgeAriaLabel"
        >{{ errorBadgeText }}</span>
      </span>
    </template>

    <template #header-actions>
      <button
        type="button"
        class="btn btn-primary sync-btn"
        :data-testid="`sync-btn-${source.id}`"
        :disabled="syncDisabled"
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

    <div v-if="detailsLoading && !detailsLoaded" class="empty-state">
      <span class="spinner" /> Loading…
    </div>

    <template v-else-if="config && schema">
      <template v-if="!isMigrated">
        <p class="source-accordion-explainer">
          This source is configured via <code>config.yaml</code>. Migrate it to the
          database to edit its settings here.
        </p>
        <button
          type="button"
          class="btn btn-primary"
          :data-testid="`migrate-btn-${source.id}`"
          :disabled="migrating"
          @click="onMigrate"
        >{{ migrating ? 'Migrating…' : 'Migrate to DB' }}</button>
      </template>

      <template v-else>
        <div v-if="showOAuthConnect" class="source-accordion-oauth">
          <OAuthConnectFlow
            v-if="isGog && data.gogStatus.authUrl"
            :auth-url="data.gogStatus.authUrl"
            expected-origin="https://login.gog.com"
            :connect-message="data.gogConnectMessage"
            help-text="Paste the redirect URL after logging in:"
            service-name="GOG Account"
            @submit="data.submitGogCode($event)"
          />
          <OAuthConnectFlow
            v-else-if="isEpic && data.epicStatus.authUrl"
            :auth-url="data.epicStatus.authUrl"
            expected-origin="https://www.epicgames.com"
            :connect-message="data.epicConnectMessage"
            help-text="Paste the authorization code from the JSON response:"
            service-name="Epic Games"
            @submit="data.submitEpicCode($event)"
          />
        </div>

        <div v-if="showTraktConnect" class="source-accordion-oauth">
          <TraktDeviceCodeFlow />
        </div>

        <SourceConfigForm
          :schema="schema.fields"
          :values="config.field_values"
          :secret-status="config.secret_status"
          :saving="savingConfig"
          :disabled="props.syncing"
          :enabled="config.enabled"
          :enable-busy="togglingEnabled"
          :save-status="saveStatus"
          :save-error="saveError"
          @save="onSaveConfig"
          @set-secret="onSetSecret"
          @clear-secret="onClearSecret"
          @toggle-enabled="onEnabledChange"
        >
          <template #actions-extra>
            <!--
              aria-live regions must exist in the DOM before content arrives,
              otherwise some screen readers (notably JAWS) skip announcements
              when the region is inserted with content already populated. Keep
              the <p> persistent and let the text update reactively.
            -->
            <p
              v-if="isGog && showOAuthDisconnect"
              class="sr-only"
              aria-live="polite"
              aria-atomic="true"
            >{{ data.gogConnectMessage }}</p>
            <p
              v-if="isEpic && showOAuthDisconnect"
              class="sr-only"
              aria-live="polite"
              aria-atomic="true"
            >{{ data.epicConnectMessage }}</p>
            <button
              v-if="showOAuthDisconnect"
              type="button"
              class="btn btn-danger"
              :data-testid="`disconnect-btn-${source.id}`"
              :aria-label="isGog ? 'Disconnect GOG' : 'Disconnect Epic Games'"
              :disabled="props.syncing"
              @click="isGog ? data.disconnectGog() : data.disconnectEpic()"
            >Disconnect</button>
            <template v-if="showTraktDisconnect">
              <span
                class="source-accordion-connected"
                data-testid="trakt-connected"
                role="status"
              >
                Trakt account connected.
              </span>
              <button
                type="button"
                class="btn btn-danger"
                data-testid="disconnect-btn-trakt"
                aria-label="Disconnect Trakt"
                :disabled="props.syncing"
                @click="data.disconnectTrakt()"
              >Disconnect</button>
            </template>
            <button
              type="button"
              class="btn btn-danger source-accordion-remove-btn"
              :data-testid="`remove-btn-${source.id}`"
              :aria-label="`Remove ${source.display_name} from the database`"
              :disabled="removing || props.syncing"
              @click="onRemove"
            >{{ removing ? 'Removing…' : 'Remove' }}</button>
          </template>
        </SourceConfigForm>
      </template>
    </template>
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
  font-weight: 600;
}

.source-accordion-status-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px var(--space-2);
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-secondary) 12%, transparent);
  /* --text-primary on the tinted background passes WCAG AA at 12px. */
  color: var(--text-primary);
  font-weight: 500;
}

/* Convey the disabled state via a softer border + secondary text colour
   on the muted parts. ``opacity`` is avoided because it composites against
   the surface and would push every text element below the WCAG 1.4.3 4.5:1
   contrast threshold. */
.source-accordion--disabled :deep(.accordion) {
  border-color: var(--border-subtle);
}

.source-accordion--disabled :deep(.accordion-trigger) {
  color: var(--text-secondary);
}

.source-accordion-explainer {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin-bottom: var(--space-3);
}

.source-accordion-oauth {
  margin-bottom: var(--space-3);
}

.source-accordion-connected {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin-right: var(--space-2);
}

.source-accordion-progress {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.source-accordion-progress-bar {
  position: relative;
  display: inline-block;
  width: 80px;
  height: 6px;
  background: var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
  vertical-align: middle;
}

.source-accordion-progress-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.2s ease;
}

@media (prefers-reduced-motion: reduce) {
  .source-accordion-progress-fill {
    transition: none;
  }
}

.source-accordion-progress-counts {
  font-variant-numeric: tabular-nums;
}

.source-accordion-progress-item {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: italic;
}

.source-accordion-error-badge {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px var(--space-2);
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-error) 18%, transparent);
  color: var(--text-primary);
  font-weight: 500;
  margin-left: var(--space-3);
}
</style>
