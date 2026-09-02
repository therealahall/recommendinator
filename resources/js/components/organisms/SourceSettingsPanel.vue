<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import SourceConfigForm from '@/components/molecules/SourceConfigForm.vue'
import {
  SAVED_STATUS_MS,
  useSecretStatus,
  type SaveStatus,
} from '@/composables/useSecretStatus'
import { useDataStore } from '@/stores/data'
import type {
  SourceConfigResponse,
  SourceFieldSchema,
  SyncSourceResponse,
} from '@/types/api'

const props = defineProps<{
  source: SyncSourceResponse
  fields: SourceFieldSchema[]
  config: SourceConfigResponse
  /** Locks the secret, enable and remove verbs, never the fields: each refetches
   *  or drops the config the form is bound to, wiping an unsaved edit. */
  verbsLocked: boolean
}>()

const emit = defineEmits<{
  /** A write the connect gate reads: a client credential, the enable flag. */
  'gate-changed': []
}>()

const data = useDataStore()
const savingConfig = ref(false)
const togglingEnabled = ref(false)
const saveStatus = ref<SaveStatus>('idle')
const saveError = ref('')
let saveStatusTimer: ReturnType<typeof setTimeout> | null = null

function control(testid: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)
}

function reason(err: unknown): string {
  return err instanceof Error ? err.message : 'Unknown error'
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
    }, SAVED_STATUS_MS)
  } catch (err) {
    saveStatus.value = 'error'
    saveError.value = reason(err)
  } finally {
    savingConfig.value = false
  }
  // Trakt's client ID is an ordinary field, so this form moves the connect gate
  // as surely as the secret verbs do.
  if (saveStatus.value === 'saved') emit('gate-changed')
}

const secret = useSecretStatus(() => emit('gate-changed'))

function onSetSecret(name: string, value: string): Promise<void> {
  return secret.run(name, () => data.setSourceSecret(props.source.id, name, value))
}

function onClearSecret(name: string): Promise<void> {
  return secret.run(name, () => data.clearSourceSecret(props.source.id, name))
}

async function onEnabledChange(value: boolean): Promise<void> {
  if (togglingEnabled.value) return
  togglingEnabled.value = true
  try {
    await data.setSourceEnabled(props.source.id, value)
    emit('gate-changed')
  } finally {
    togglingEnabled.value = false
  }
}

const removing = ref(false)
const removeConfirming = ref(false)
const removeError = ref('')

const removeQuestion = computed(
  () =>
    `Remove "${props.source.display_name}" from the database? This drops ` +
    'every stored secret for this source. The original config.yaml entry ' +
    '(if any) will reappear next reload.',
)

function askRemove(): void {
  if (removing.value || props.verbsLocked) return
  removeConfirming.value = true
}

async function onRemove(): Promise<void> {
  removeConfirming.value = false
  if (removing.value) return
  removing.value = true
  removeError.value = ''
  try {
    await data.deleteSource(props.source.id)
    await nextTick()
    // This whole accordion went with the source, so the keyboard lands on the
    // panel that outlived it rather than at <body> (WCAG 2.4.3).
    control('sync-sources-panel')?.focus()
  } catch (err) {
    removeError.value = reason(err)
    await nextTick()
    control(`remove-error-${props.source.id}`)?.focus()
  } finally {
    removing.value = false
  }
}

onBeforeUnmount(() => {
  if (saveStatusTimer) clearTimeout(saveStatusTimer)
})
</script>

<template>
  <SourceConfigForm
    :schema="fields"
    :values="config.field_values"
    :secret-status="config.secret_status"
    :source-name="source.display_name"
    :secret-save="secret.status"
    :secret-save-error="secret.error"
    :saving="savingConfig"
    :verbs-locked="verbsLocked"
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
        type="button"
        class="btn btn-danger"
        :data-testid="`remove-btn-${source.id}`"
        :aria-label="`Remove ${source.display_name} from the database`"
        :aria-disabled="removing || verbsLocked || undefined"
        @click="askRemove"
      >{{ removing ? 'Removing…' : 'Remove' }}</button>
    </template>
  </SourceConfigForm>

  <ConfirmPanel
    v-if="removeConfirming"
    :message="removeQuestion"
    confirm-label="Remove"
    cancel-label="Keep it"
    destructive
    @cancel="removeConfirming = false"
    @confirm="onRemove"
  />

  <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
  <p
    class="state state--error source-settings-error focus-fallback"
    :data-testid="`remove-error-${source.id}`"
    role="alert"
    tabindex="-1"
  >{{ removeError }}</p>
</template>

<style scoped>
.source-settings-error:not(:empty) {
  margin-top: var(--space-3);
}
</style>
