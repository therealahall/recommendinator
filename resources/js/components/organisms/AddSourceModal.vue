<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useFocusTrap } from '@/composables/useFocusTrap'
import { useDataStore } from '@/stores/data'
import type { PluginInfoResponse, SourceCreateRequest } from '@/types/api'

const data = useDataStore()
const modalContent = ref<HTMLElement | null>(null)
useFocusTrap(modalContent, () => emit('close'))

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
})

const selectedPlugin = computed<PluginInfoResponse | undefined>(() =>
  data.availablePlugins.find((p) => p.name === pluginName.value),
)

const visibleFields = computed(() =>
  selectedPlugin.value
    ? selectedPlugin.value.fields.filter((f) => !f.sensitive)
    : [],
)

// Mirrors ``_SOURCE_ID_RE`` in ``src/web/sync_sources.py``. Keep both in
// sync — the server-side regex is the authoritative gate (the API rejects
// mismatches with 400 ``invalid_id``); this client-side check is purely a
// UX affordance for the Create button's disabled state.
const idIsValid = computed(() => /^[a-z][a-z0-9_]*$/.test(sourceId.value))

async function submit(): Promise<void> {
  errorMessage.value = ''
  if (!pluginName.value || !idIsValid.value || submitting.value) return
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
  <div class="add-source-modal" @click.self="emit('close')">
    <div
      ref="modalContent"
      class="add-source-modal-content"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-source-title"
      tabindex="-1"
    >
      <h3 id="add-source-title">Add data source</h3>
      <p class="help-text">
        Create a new database-backed source. Sensitive fields (API keys, OAuth
        tokens) can be set after the source is created using the Replace
        action in the source's settings panel.
      </p>

      <div class="add-source-field">
        <label for="add-source-plugin">Plugin</label>
        <select
          id="add-source-plugin"
          v-model="pluginName"
          :disabled="submitting"
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
      </div>

      <div class="add-source-field">
        <label for="add-source-id">Source id</label>
        <input
          id="add-source-id"
          v-model="sourceId"
          type="text"
          placeholder="e.g. my_books"
          :disabled="submitting"
          autocomplete="off"
          spellcheck="false"
        />
        <p class="help-text">
          Lowercase letters, digits, and underscores. Must start with a letter.
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

      <div class="add-source-actions">
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="submitting"
          @click="emit('close')"
        >Cancel</button>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="add-source-submit"
          :disabled="!pluginName || !idIsValid || submitting"
          @click="submit"
        >{{ submitting ? 'Creating…' : 'Create' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.add-source-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: var(--space-3);
}

.add-source-modal-content {
  background: var(--bg-card, var(--surface));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  width: 100%;
  max-width: 38rem;
  max-height: 90vh;
  overflow: auto;
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
.add-source-field select {
  padding: var(--space-2) var(--space-3);
  background: var(--bg-card, var(--surface));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
}

.add-source-field input:focus-visible,
.add-source-field select:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
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

.add-source-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-default);
}
</style>
