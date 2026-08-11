<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
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
const oauthStatusFailed = ref(false)
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
    await loadOAuthState()
    detailsLoaded.value = true
  } finally {
    detailsLoading.value = false
  }
}

// A failed status read is tracked, not swallowed: the fallback reads as "not
// connected", which offers a Connect button and a hint naming a remedy that
// may have nothing to do with the failure.
async function loadOAuthState(): Promise<void> {
  if (!isOAuthSource.value) return
  try {
    await data.loadOAuthStatus(props.source.id, plugin.value)
    oauthStatusFailed.value = false
  } catch {
    oauthStatusFailed.value = true
  }
}

async function onToggleExpanded(value: boolean): Promise<void> {
  expanded.value = value
  if (!value) return
  // Accordion.vue hides the body with `hidden` rather than unmounting it, so
  // a message left from the last visit would re-enter the accessibility tree
  // already populated — read as page content, never as a status (WCAG 4.1.3).
  data.setOAuthMessage(props.source.id, '')
  await ensureDetails()
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

/** The account each OAuth plugin connects, for the disconnect button's label. */
const OAUTH_SERVICE_NAME: Record<string, string> = {
  gog: 'GOG',
  epic_games: 'Epic Games',
  trakt: 'Trakt',
}

// Keyed on the plugin, never the source id: a GOG source the user named
// "gog_work" runs the same connect flow as one named "gog".
const plugin = computed(() => config.value?.plugin ?? '')
const isGog = computed(() => plugin.value === 'gog')
const isEpic = computed(() => plugin.value === 'epic_games')
const isTrakt = computed(() => plugin.value === 'trakt')
const isOAuthSource = computed(() => plugin.value in OAUTH_SERVICE_NAME)
// Named for the source, not just the service: two expanded gog panels would
// otherwise offer two buttons with the identical accessible name.
const disconnectLabel = computed(
  () =>
    `Disconnect ${props.source.display_name} from ` +
    `${OAUTH_SERVICE_NAME[plugin.value]}`,
)
const retryStatusLabel = computed(
  () => `Retry the connection status check for ${props.source.display_name}`,
)
const oauthPanelLabel = computed(() => `${props.source.display_name} connection`)
const oauth = computed(() => data.oauthStatusFor(props.source.id))
const oauthMessage = computed(() => data.oauthMessages[props.source.id] ?? '')
const showOAuthConnect = computed(
  () =>
    isMigrated.value &&
    !oauthStatusFailed.value &&
    (isGog.value || isEpic.value) &&
    !oauth.value.connected &&
    !!oauth.value.authUrl,
)
const showTraktConnect = computed(
  () =>
    isMigrated.value &&
    !oauthStatusFailed.value &&
    isTrakt.value &&
    !oauth.value.connected,
)
const showDisconnect = computed(
  () => isMigrated.value && isOAuthSource.value && oauth.value.connected,
)

const oauthPanel = ref<HTMLElement | null>(null)
const oauthRetrying = ref(false)

// Connect, disconnect and a recovered status read each swap out part of the
// panel, and each can take the control holding focus with it — dropping the
// keyboard user to <body> (WCAG 2.4.3). One place decides, keyed on whether
// that element actually went away: a refused disconnect leaves its button
// mounted and must not throw the user out of it.
watch([() => oauth.value.connected, oauthStatusFailed], () => {
  const focused = document.activeElement
  void nextTick(() => {
    if (!(focused instanceof HTMLElement) || focused.isConnected) return
    oauthPanel.value?.focus()
  })
})

async function onDisconnect(): Promise<void> {
  if (isGog.value) await data.disconnectGog(props.source.id)
  else if (isEpic.value) await data.disconnectEpic(props.source.id)
  else if (isTrakt.value) await data.disconnectTrakt(props.source.id)
}

async function onRetryStatus(): Promise<void> {
  if (oauthRetrying.value) return
  oauthRetrying.value = true
  data.setOAuthMessage(props.source.id, 'Rechecking the connection status…')
  await loadOAuthState()
  oauthRetrying.value = false
  // A second failure changes nothing else on screen, so without a word here
  // the click has no perceivable outcome at all.
  data.setOAuthMessage(
    props.source.id,
    oauthStatusFailed.value
      ? 'Still could not read the connection status. Try again in a moment.'
      : 'Connection status updated.',
  )
}

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
        <!--
          Rendered for every OAuth source whatever its connection state: it is
          the focus target when an outcome removes the button that had focus.
        -->
        <template v-if="isOAuthSource">
          <div
            ref="oauthPanel"
            class="source-accordion-oauth"
            role="group"
            :aria-label="oauthPanelLabel"
            tabindex="-1"
          >
            <template v-if="showOAuthConnect">
              <OAuthConnectFlow
                v-if="isGog"
                :source-id="source.id"
                :auth-url="oauth.authUrl"
                expected-origin="https://login.gog.com"
                help-text="Paste the redirect URL after logging in:"
                service-name="GOG Account"
                @submit="data.submitGogCode(source.id, $event)"
              />
              <OAuthConnectFlow
                v-else-if="isEpic"
                :source-id="source.id"
                :auth-url="oauth.authUrl"
                expected-origin="https://www.epicgames.com"
                help-text="Paste the authorization code from the JSON response:"
                service-name="Epic Games"
                @submit="data.submitEpicCode(source.id, $event)"
              />
            </template>

            <TraktDeviceCodeFlow v-if="showTraktConnect" :source-id="source.id" />

            <!--
              Plain content, not role="alert": it can only appear as the body
              first renders, where an alert arrives already populated and is
              read as page content. The retry outcome goes to the region below,
              which is mounted and silent before it has anything to say.
            -->
            <div v-if="oauthStatusFailed" class="source-accordion-oauth-error">
              <p data-testid="oauth-status-error">
                Could not read this source's connection status.
              </p>
              <button
                type="button"
                class="btn btn-secondary"
                data-testid="oauth-status-retry"
                :aria-label="retryStatusLabel"
                :aria-disabled="oauthRetrying || undefined"
                @click="onRetryStatus"
              >{{ oauthRetrying ? 'Retrying…' : 'Retry' }}</button>
            </div>
          </div>

          <!--
            The one live region for the whole OAuth lifecycle — connect,
            disconnect, retry and every refusal. Visible, because a refused
            disconnect leaves the button in place and changes nothing else on
            screen. Outside the focus target above, so landing there does not
            read these words a second time.
          -->
          <p
            class="source-accordion-oauth-message"
            data-testid="oauth-message"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >{{ oauthMessage }}</p>
        </template>

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
            <span
              v-if="showDisconnect && isTrakt"
              class="source-accordion-connected"
              data-testid="trakt-connected"
            >
              Trakt account connected.
            </span>
            <button
              v-if="showDisconnect"
              type="button"
              class="btn btn-danger"
              :data-testid="`disconnect-btn-${source.id}`"
              :aria-label="disconnectLabel"
              :disabled="props.syncing"
              @click="onDisconnect"
            >Disconnect</button>
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

/* Focused only programmatically, after an outcome removes the button that had
   focus, so the ring would read as a stray highlight. */
.source-accordion-oauth:focus {
  outline: none;
}

.source-accordion-oauth-message {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

/* Silent, the region still has to stay in the accessibility tree, so it earns
   its spacing only once it says something. */
.source-accordion-oauth-message:not(:empty) {
  margin-bottom: var(--space-3);
}

/* A connected source keeps this panel mounted purely as that focus target,
   with nothing in it to space away from the form below. */
.source-accordion-oauth:not(:has(*)) {
  margin-bottom: 0;
}

.source-accordion-oauth-error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.source-accordion-oauth-error p {
  margin: 0;
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
