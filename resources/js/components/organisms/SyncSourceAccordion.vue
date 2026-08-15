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
const gateRefreshing = ref(false)
const migrating = ref(false)
const savingConfig = ref(false)
const togglingEnabled = ref(false)
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const saveStatus = ref<SaveStatus>('idle')
const saveError = ref('')
let saveStatusTimer: ReturnType<typeof setTimeout> | null = null

/** Retry and a gate-changing write run the same re-read, so they say the same
 *  words for it. */
const RECHECKING_STATUS = 'Rechecking the connection status…'
const STATUS_UPDATED = 'Connection status updated.'

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
  // Trakt's client ID is an ordinary field, so this form moves the connect
  // gate as surely as the secret verbs do. Out here rather than in the try:
  // the recheck's own await would hold "Saving…" on a button whose status
  // pill already reads "Saved ✓".
  if (saveStatus.value === 'saved') await refreshConnectGate()
}

let gateGeneration = 0

// The Connect button reads the OAuth status; the hint under it reads the
// source's own settings. Only the settings half of that pair moves when the
// user enables the source or stores a client credential, so without this the
// button stays dead under a hint that has moved on to naming a different
// remedy — and nothing on screen changes to say so.
async function refreshConnectGate(): Promise<void> {
  if (!isOAuthSource.value) return
  const generation = ++gateGeneration
  gateRefreshing.value = true
  data.setOAuthMessage(props.source.id, RECHECKING_STATUS)
  await loadOAuthState()
  // The secret verbs stay live through an enable, so two rechecks overlap. A
  // refresh overtaken by a later one read a gate that has already moved again:
  // releasing the hold on its result is how the stale remedy returns.
  if (generation !== gateGeneration) return
  gateRefreshing.value = false
  data.setOAuthMessage(
    props.source.id,
    oauthStatusFailed.value
      ? 'Could not read the connection status. Try again in a moment.'
      : STATUS_UPDATED,
  )
}

async function onSetSecret(name: string, value: string): Promise<void> {
  await data.setSourceSecret(props.source.id, name, value)
  await refreshConnectGate()
}

async function onClearSecret(name: string): Promise<void> {
  await data.clearSourceSecret(props.source.id, name)
  await refreshConnectGate()
}

async function onEnabledChange(value: boolean): Promise<void> {
  if (togglingEnabled.value) return
  togglingEnabled.value = true
  try {
    await data.setSourceEnabled(props.source.id, value)
    await refreshConnectGate()
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
const oauthPanelLabel = computed(() => `${props.source.display_name} connection`)
const connectedLabel = computed(
  () => `${OAUTH_SERVICE_NAME[plugin.value]} account connected.`,
)
const oauth = computed(() => data.oauthStatusFor(props.source.id))
const oauthMessage = computed(() => data.oauthMessages[props.source.id] ?? '')
// Not gated on the auth URL: a source the server will not connect gets one
// disabled button and a hint naming the remedy, where dropping the whole block
// left a named, empty group announcing nothing.
const showOAuthConnect = computed(
  () =>
    isMigrated.value &&
    !oauthStatusFailed.value &&
    (isGog.value || isEpic.value) &&
    !oauth.value.connected,
)
const showTraktConnect = computed(
  () =>
    isMigrated.value &&
    !oauthStatusFailed.value &&
    isTrakt.value &&
    !oauth.value.connected,
)
// Neither flow can name its own remedy: Trakt's `enabled` folds "disabled" in
// with "no client credentials", and Epic nulls the auth URL when its builder
// throws while enabled. Only the enable flag tells those apart.
const connectHint = computed(() => {
  // Mid-refresh the two halves disagree, the settings one having moved first.
  // Naming a remedy from it alone is how a Trakt source one click from
  // connectable was told to add the credentials it already had.
  if (gateRefreshing.value) return RECHECKING_STATUS
  if (!config.value?.enabled) {
    return 'Enable this source in the settings below before you can connect.'
  }
  if (isTrakt.value) {
    return (
      'Add the Trakt client ID and client secret in the settings below ' +
      'before you can connect.'
    )
  }
  return 'The service did not return a sign-in link. Try again in a moment.'
})
// An unreadable status asserts nothing: the cached flag is what the server said
// before, and claiming a connection next to "could not read the status" leaves
// the user no way to tell which statement is current.
const showConnected = computed(
  () => !oauthStatusFailed.value && oauth.value.connected,
)
const showDisconnect = computed(
  () => isMigrated.value && isOAuthSource.value && showConnected.value,
)

const oauthPanel = ref<HTMLElement | null>(null)
const oauthRetrying = ref(false)
// Tracks the visible label, which speech-input users say back (WCAG 2.5.3).
const retryStatusLabel = computed(
  () =>
    `${oauthRetrying.value ? 'Retrying' : 'Retry'} the connection status ` +
    `check for ${props.source.display_name}`,
)

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

// Only the status re-read rejects out of these: a refused connect or disconnect
// is reported in the live region instead. The server has already acted by then,
// so the cached flag is stale — showing the status as unknown puts one
// statement on screen, with the Retry that can settle it.
async function onDisconnect(): Promise<void> {
  try {
    if (isGog.value) await data.disconnectGog(props.source.id)
    else if (isEpic.value) await data.disconnectEpic(props.source.id)
    else if (isTrakt.value) await data.disconnectTrakt(props.source.id)
  } catch {
    oauthStatusFailed.value = true
  }
}

async function onSubmitCode(code: string): Promise<void> {
  try {
    if (isGog.value) await data.submitGogCode(props.source.id, code)
    else if (isEpic.value) await data.submitEpicCode(props.source.id, code)
  } catch {
    oauthStatusFailed.value = true
  }
}

async function onRetryStatus(): Promise<void> {
  if (oauthRetrying.value) return
  oauthRetrying.value = true
  data.setOAuthMessage(props.source.id, RECHECKING_STATUS)
  await loadOAuthState()
  oauthRetrying.value = false
  // A second failure changes nothing else on screen, so without a word here
  // the click has no perceivable outcome at all.
  data.setOAuthMessage(
    props.source.id,
    oauthStatusFailed.value
      ? 'Still could not read the connection status. Try again in a moment.'
      : STATUS_UPDATED,
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

// Filtered by name, never taken whole: an "All Sources" job carries every
// source's failures, and the remedy for one source is wrong on the next row.
const sourceErrors = computed<string[]>(() => {
  if (props.syncing || !props.job) return []
  return props.job.errors
    .filter((entry) => entry.source === props.source.display_name)
    .map((entry) => entry.message)
})

const errorsLabel = computed<string>(
  () => `Last sync errors for ${props.source.display_name}`,
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

    <!--
      Plain content, not a live region: it renders on the poll that ends the
      sync, and a region arriving already populated is read as page content
      rather than a status change. The page-level sync banner announces
      (WCAG 4.1.3).
    -->
    <template #notice>
      <ul
        v-if="sourceErrors.length"
        class="source-accordion-errors"
        data-testid="source-sync-errors"
        :aria-label="errorsLabel"
      >
        <li v-for="(message, index) in sourceErrors" :key="index">{{ message }}</li>
      </ul>
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
            <!--
              The panel is where focus lands, so the connected state needs
              something in it: an empty group gives a sighted keyboard user
              nothing on screen to read the announcement against.
            -->
            <p
              v-if="showConnected"
              class="source-accordion-oauth-connected"
              data-testid="oauth-connected"
            >{{ connectedLabel }}</p>

            <template v-if="showOAuthConnect">
              <OAuthConnectFlow
                v-if="isGog"
                :source-id="source.id"
                :source-name="source.display_name"
                :auth-url="oauth.authUrl"
                expected-origin="https://login.gog.com"
                help-text="Paste the redirect URL after logging in:"
                service-name="GOG Account"
                :connect-hint="connectHint"
                @submit="onSubmitCode"
              />
              <OAuthConnectFlow
                v-else-if="isEpic"
                :source-id="source.id"
                :source-name="source.display_name"
                :auth-url="oauth.authUrl"
                expected-origin="https://www.epicgames.com"
                help-text="Paste the authorization code from the JSON response:"
                service-name="Epic Games"
                :connect-hint="connectHint"
                @submit="onSubmitCode"
              />
            </template>

            <TraktDeviceCodeFlow
              v-if="showTraktConnect"
              :source-id="source.id"
              :source-name="source.display_name"
              :connect-hint="connectHint"
            />

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
            The one live region for the whole OAuth lifecycle. Visible: a
            refused disconnect changes nothing else on screen. Outside the
            focus target, so landing there does not repeat it. Named, since
            several panels announce and nothing collapses the others.
          -->
          <p
            class="source-accordion-oauth-message"
            data-testid="oauth-message"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          ><span v-if="oauthMessage" class="sr-only">{{ source.display_name }}: </span>{{ oauthMessage }}</p>
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

/* Pointer focus only, mirroring .main-content in base.css. The panel is focused
   programmatically, which propagates :focus-visible from the control the user
   just activated — so a keyboard disconnect keeps the ring that says where
   focus went (2.4.7), and a mouse one never draws it. */
.source-accordion-oauth:focus:not(:focus-visible) {
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

.source-accordion-oauth-connected {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
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

.source-accordion-errors {
  margin: 0;
  padding: var(--space-2) var(--space-4);
  list-style: none;
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 18%, transparent);
}

.source-accordion-errors li + li {
  margin-top: var(--space-1);
}
</style>
