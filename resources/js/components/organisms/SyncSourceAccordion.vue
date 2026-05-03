<script setup lang="ts">
import { computed, ref } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import SourceConfigForm from '@/components/molecules/SourceConfigForm.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import { useDataStore } from '@/stores/data'
import type { SyncSourceResponse } from '@/types/api'

const props = defineProps<{
  source: SyncSourceResponse
  syncing: boolean
  disabled: boolean
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
  savingConfig.value = true
  try {
    await data.updateSourceConfig(props.source.id, values)
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
  await data.setSourceEnabled(props.source.id, value)
}

const isGog = computed(() => props.source.id === 'gog')
const isEpic = computed(() => props.source.id === 'epic_games')
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

const syncDisabled = computed(() => props.syncing || props.disabled)
const syncLabel = computed(() => (props.syncing ? 'Syncing…' : 'Sync'))
</script>

<template>
  <Accordion
    :id="source.id"
    :expanded="expanded"
    @update:expanded="onToggleExpanded"
  >
    <template #header>
      <span class="source-accordion-name">{{ source.display_name }}</span>
    </template>

    <template #header-actions>
      <button
        type="button"
        class="btn btn-primary sync-btn"
        :data-testid="`sync-btn-${source.id}`"
        :disabled="syncDisabled"
        :aria-label="
          props.syncing
            ? `Syncing ${source.display_name} — in progress`
            : props.disabled
            ? `Sync ${source.display_name} — another sync is in progress`
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
        <div
          class="source-accordion-toggle"
          :data-testid="`enabled-toggle-${source.id}`"
        >
          <ToggleSwitch
            :model-value="config.enabled"
            label="Enabled"
            @update:model-value="onEnabledChange"
          />
        </div>

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

        <div v-if="showOAuthDisconnect" class="source-accordion-oauth">
          <!--
            aria-live regions must exist in the DOM before content arrives,
            otherwise some screen readers (notably JAWS) skip announcements
            when the region is inserted with content already populated. Keep
            the &lt;p&gt; persistent and let the text update reactively.
          -->
          <p
            v-if="isGog"
            class="sr-only"
            aria-live="polite"
            aria-atomic="true"
          >{{ data.gogConnectMessage }}</p>
          <p
            v-if="isEpic"
            class="sr-only"
            aria-live="polite"
            aria-atomic="true"
          >{{ data.epicConnectMessage }}</p>
          <button
            type="button"
            class="btn btn-danger"
            :data-testid="`disconnect-btn-${source.id}`"
            :aria-label="isGog ? 'Disconnect GOG' : 'Disconnect Epic Games'"
            :disabled="props.syncing"
            @click="isGog ? data.disconnectGog() : data.disconnectEpic()"
          >Disconnect</button>
        </div>

        <SourceConfigForm
          :schema="schema.fields"
          :values="config.field_values"
          :secret-status="config.secret_status"
          :saving="savingConfig"
          :disabled="props.disabled"
          @save="onSaveConfig"
          @set-secret="onSetSecret"
          @clear-secret="onClearSecret"
        />
      </template>
    </template>
  </Accordion>
</template>

<style scoped>
.source-accordion-name {
  font-weight: 600;
}

.source-accordion-explainer {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin-bottom: var(--space-3);
}

.source-accordion-toggle {
  margin-bottom: var(--space-3);
}

.source-accordion-oauth {
  margin-bottom: var(--space-3);
}
</style>
