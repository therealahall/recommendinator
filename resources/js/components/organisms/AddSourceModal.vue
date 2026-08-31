<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useDiscardGuard } from '@/composables/useDiscardGuard'
import { useDataStore } from '@/stores/data'
import ModalDialog from '@/components/atoms/ModalDialog.vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import type { PluginInfoResponse, SourceCreateRequest } from '@/types/api'

const data = useDataStore()
const dialog = ref<InstanceType<typeof ModalDialog> | null>(null)
const modalContent = computed(() => dialog.value?.surface ?? null)

const emit = defineEmits<{
  created: [sourceId: string]
  close: []
}>()

const pluginName = ref<string>('')
const sourceId = ref('')
const enabled = ref(true)
const fieldValues = ref<Record<string, string>>({})
const submitting = ref(false)
const errorMessage = ref('')

const sourceIdEdited = ref(false)

function applyPluginDefaultId(): void {
  if (!sourceIdEdited.value && selectedPlugin.value) {
    sourceId.value = selectedPlugin.value.name
  }
}

function onSourceIdInput(event: Event): void {
  sourceIdEdited.value = true
  sourceId.value = (event.target as HTMLInputElement).value
}

const dirty = computed(
  () =>
    sourceIdEdited.value ||
    !enabled.value ||
    Object.values(fieldValues.value).some((value) => value !== undefined && value !== ''),
)

const { confirming, requestClose, keepEditing } = useDiscardGuard(
  dirty,
  () => emit('close'),
  modalContent,
)

onMounted(async () => {
  if (data.availablePlugins.length === 0) {
    try {
      await data.loadAvailablePlugins()
    } catch (err) {
      errorMessage.value =
        err instanceof Error ? err.message : 'Failed to load plugins'
    }
  }
  if (data.availablePlugins.length > 0 && !pluginName.value) {
    pluginName.value = data.availablePlugins[0].name
  }
  applyPluginDefaultId()
})

const selectedPlugin = computed<PluginInfoResponse | undefined>(() =>
  data.availablePlugins.find((p) => p.name === pluginName.value),
)

const visibleFields = computed(() =>
  selectedPlugin.value
    ? selectedPlugin.value.fields.filter((f) => !f.sensitive)
    : [],
)

const sensitiveFields = computed(() =>
  selectedPlugin.value
    ? selectedPlugin.value.fields.filter((f) => f.sensitive)
    : [],
)

// Mirrors ``_SOURCE_ID_RE`` in ``src/sources/service.py``. Keep both in
// sync — the server-side regex is the authoritative gate (the API rejects
// mismatches with 400 ``invalid_id``); this client-side check is purely a
// UX affordance for the Create button's disabled state.
const idIsValid = computed(() => /^[a-z][a-z0-9_-]*$/.test(sourceId.value))

const idError = computed(() =>
  sourceId.value !== '' && !idIsValid.value
    ? 'Source id must start with a lowercase letter and use only lowercase ' +
      'letters, digits, underscores, and hyphens.'
    : '',
)

// Associate the id input with whichever guidance is on screen: the inline
// error when the id is invalid, otherwise the static help text. Only one
// renders at a time, so aria-describedby names exactly the visible element.
const sourceIdDescribedBy = computed(() =>
  idError.value !== '' ? 'add-source-id-error' : 'add-source-id-help',
)

// Surfacing these explains why Create is disabled instead of leaving a silent dead-end.
const missingRequiredFields = computed(() =>
  (selectedPlugin.value?.fields ?? [])
    .filter((field) => field.required && !fieldValues.value[field.name])
    .map((field) => field.name),
)

const blockers = computed(() => {
  const missing: string[] = []
  if (!pluginName.value) missing.push('a plugin')
  if (sourceId.value === '') missing.push('a source id')
  return [...missing, ...missingRequiredFields.value]
})

const isValid = computed(
  () =>
    !!pluginName.value &&
    sourceId.value !== '' &&
    idIsValid.value &&
    missingRequiredFields.value.length === 0,
)

// Never natively disabled: `submitting` flips under the user's own focus, and a
// disabled Create is unreachable by Tab, so the reason it refuses goes unread.
const canSubmit = computed(() => isValid.value && !submitting.value)

// Whichever guidance is on screen explaining what Create is still waiting for.
const blockedBy = computed(() => {
  if (isValid.value) return undefined
  const ids: string[] = []
  if (idError.value !== '') ids.push('add-source-id-error')
  if (blockers.value.length > 0) ids.push('add-source-missing-fields')
  return ids.join(' ') || undefined
})

// aria-disabled does not block activation the way native disabled does, so
// Cancel has to drop the click itself rather than closing mid-request.
function onCancel(): void {
  if (submitting.value) return
  requestClose()
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  errorMessage.value = ''
  const values: Record<string, unknown> = {}
  for (const field of visibleFields.value) {
    const raw = fieldValues.value[field.name]
    if (raw === undefined || raw === '') continue
    if (field.field_type === 'int') {
      const n = parseInt(raw, 10)
      if (Number.isFinite(n)) values[field.name] = n
    } else if (field.field_type === 'float') {
      const n = parseFloat(raw)
      if (Number.isFinite(n)) values[field.name] = n
    } else if (field.field_type === 'bool') {
      values[field.name] = raw === 'true'
    } else if (field.field_type === 'list') {
      values[field.name] = raw
        .split(',')
        .map((part) => part.trim())
        .filter((part) => part.length > 0)
    } else {
      values[field.name] = raw
    }
  }

  const payload: SourceCreateRequest = {
    id: sourceId.value,
    plugin: pluginName.value,
    values,
    enabled: enabled.value,
  }

  submitting.value = true
  try {
    const created = await data.createSource(payload)
    for (const field of sensitiveFields.value) {
      const secret = fieldValues.value[field.name]
      if (secret === undefined || secret === '') continue
      try {
        await data.setSourceSecret(created.source_id, field.name, secret)
      } catch (err) {
        // The source now exists but this secret failed to save.
        emit('created', created.source_id)
        errorMessage.value =
          (err instanceof Error ? err.message : 'Failed to save secret') +
          ` — the source "${created.source_id}" was created, but its ` +
          `"${field.name}" could not be saved. Set it from the source ` +
          'settings panel.'
        return
      }
    }
    emit('created', created.source_id)
    emit('close')
  } catch (err) {
    errorMessage.value =
      err instanceof Error ? err.message : 'Failed to create source'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <ModalDialog ref="dialog" labelled-by="add-source-title" wide @dismiss="requestClose">
    <div class="add-source-body">
      <h3 id="add-source-title">Add data source</h3>
      <p class="help-text">
        Create a new database-backed source. Passwords and API keys entered
        below are stored encrypted and set securely — they are never written to
        the plaintext config.
      </p>

      <div class="add-source-field">
        <label for="add-source-plugin">Plugin</label>
        <!-- Described by the failure list: without it the picker reads as the
             complete set of plugins, and a missing one as unsupported. -->
        <select
          id="add-source-plugin"
          v-model="pluginName"
          :disabled="submitting"
          :aria-describedby="
            data.pluginImportErrors.length ? 'add-source-import-errors' : undefined
          "
          @change="applyPluginDefaultId"
        >
          <option
            v-for="plugin in data.availablePlugins"
            :key="plugin.name"
            :value="plugin.name"
          >{{ plugin.display_name }} ({{ plugin.name }})</option>
        </select>
        <p v-if="selectedPlugin" class="help-text">
          {{ selectedPlugin.description }}
        </p>
        <!--
          Plain content, not a live region: it is already populated when the
          dialog opens, and a region arriving that way is read as content
          rather than a status change (WCAG 4.1.3).
        -->
        <ul
          v-if="data.pluginImportErrors.length"
          id="add-source-import-errors"
          class="add-source-import-errors"
          data-testid="add-source-import-errors"
          role="list"
        >
          <li v-for="failure in data.pluginImportErrors" :key="failure.module">
            Plugin module "{{ failure.module }}" is missing from this list
            because it failed to load. {{ failure.reason }}
          </li>
        </ul>
      </div>

      <div class="add-source-field">
        <label for="add-source-id">
          Source id
          <span aria-hidden="true">*</span>
        </label>
        <input
          id="add-source-id"
          :value="sourceId"
          type="text"
          placeholder="e.g. my_books"
          required
          :disabled="submitting"
          :aria-invalid="idError !== ''"
          :aria-describedby="sourceIdDescribedBy"
          autocomplete="off"
          spellcheck="false"
          @input="onSourceIdInput"
        />
        <p
          v-if="idError"
          id="add-source-id-error"
          class="add-source-field-error"
          data-testid="add-source-id-error"
          role="alert"
        >{{ idError }}</p>
        <p v-else id="add-source-id-help" class="help-text">
          Lowercase letters, digits, underscores, and hyphens. Must start with a
          letter.
        </p>
      </div>

      <div
        v-for="field in visibleFields"
        :key="field.name"
        class="add-source-field"
      >
        <label :for="`add-source-field-${field.name}`">
          {{ field.name }}
          <span v-if="field.required" aria-hidden="true">*</span>
        </label>
        <select
          v-if="field.field_type === 'bool'"
          :id="`add-source-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :required="field.required"
          :disabled="submitting"
        >
          <option value="">— default —</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        <input
          v-else
          :id="`add-source-field-${field.name}`"
          v-model="fieldValues[field.name]"
          :type="
            field.field_type === 'int' || field.field_type === 'float'
              ? 'number'
              : 'text'
          "
          :step="field.field_type === 'float' ? 'any' : '1'"
          :required="field.required"
          :disabled="submitting"
          :placeholder="
            field.field_type === 'list' ? 'comma,separated,values' : ''
          "
        />
        <p v-if="field.description" class="help-text">{{ field.description }}</p>
      </div>

      <div
        v-for="field in sensitiveFields"
        :key="`secret-${field.name}`"
        class="add-source-field"
      >
        <label :for="`add-source-secret-${field.name}`">
          {{ field.name }}
          <span v-if="field.required" aria-hidden="true">*</span>
        </label>
        <input
          :id="`add-source-secret-${field.name}`"
          v-model="fieldValues[field.name]"
          :data-testid="`add-source-secret-${field.name}`"
          type="password"
          autocomplete="new-password"
          :required="field.required"
          :disabled="submitting"
          :aria-describedby="
            field.description
              ? `add-source-secret-desc-${field.name} add-source-secret-note-${field.name}`
              : `add-source-secret-note-${field.name}`
          "
        />
        <p
          v-if="field.description"
          :id="`add-source-secret-desc-${field.name}`"
          class="help-text"
        >{{ field.description }}</p>
        <p
          :id="`add-source-secret-note-${field.name}`"
          class="help-text"
        >Stored encrypted and set securely after creation.</p>
      </div>

      <p
        v-if="blockers.length > 0"
        id="add-source-missing-fields"
        class="add-source-hint"
        data-testid="add-source-missing-fields"
      >
        Required to create:
        {{ blockers.join(', ') }}.
      </p>

      <label class="add-source-toggle">
        <input
          v-model="enabled"
          type="checkbox"
          :disabled="submitting"
        />
        Enabled
      </label>

      <p
        v-if="errorMessage"
        class="add-source-error"
        role="alert"
      >{{ errorMessage }}</p>

      <ConfirmPanel
        v-if="confirming"
        message="Discard your unsaved changes?"
        confirm-label="Discard"
        cancel-label="Keep editing"
        @cancel="keepEditing"
        @confirm="emit('close')"
      />
    </div>

    <template #actions>
      <!-- Cancel stays focusable while submitting so the dialog always has at
           least one tabbable element and the focus trap cannot collapse. -->
      <button
        type="button"
        class="btn btn-secondary"
        :aria-disabled="submitting || undefined"
        @click="onCancel"
      >Cancel</button>
      <button
        type="button"
        class="btn btn-primary"
        data-testid="add-source-submit"
        :aria-disabled="!canSubmit || undefined"
        :aria-describedby="blockedBy"
        @click="submit"
      >{{ submitting ? 'Creating…' : 'Create' }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.add-source-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.add-source-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.add-source-field label {
  font-weight: 600;
  font-size: var(--text-sm);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.add-source-field input[type="text"],
.add-source-field input[type="number"],
.add-source-field input[type="password"],
.add-source-field select {
  padding: var(--space-2) var(--space-3);
  background: var(--bg-card);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
}

.add-source-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.add-source-error {
  margin: 0;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
  /* --text-primary on a 35% error tint clears WCAG 1.4.3 4.5:1 against
     --bg-card; semantic colour reads through the background. */
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.add-source-field-error {
  margin: 0;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: 600;
}

.add-source-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.add-source-import-errors {
  margin: 0;
  padding: var(--space-2);
  list-style: none;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--color-error) 35%, transparent);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.add-source-import-errors li + li {
  margin-top: var(--space-1);
}
</style>
