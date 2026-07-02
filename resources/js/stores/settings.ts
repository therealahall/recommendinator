import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import { useApi, ApiError } from '@/composables/useApi'
import type {
  SettingsResponse,
  SettingsSection,
  SettingValidationError,
} from '@/types/api'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

/** Pull the `{ key, reason }` out of a 422 body (FastAPI wraps it as
 *  `{ detail: { key, reason } }`). Returns null if the shape doesn't match. */
function extractValidationError(body: unknown): SettingValidationError | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const detail = (body as { detail: unknown }).detail
  if (!detail || typeof detail !== 'object') return null
  const { key, reason } = detail as Record<string, unknown>
  if (typeof key === 'string' && typeof reason === 'string') {
    return { key, reason }
  }
  return null
}

export const useSettingsStore = defineStore('settings', () => {
  const api = useApi()

  const sections = ref<SettingsSection[]>([])
  const loading = ref(false)
  const loadError = ref('')

  // Keyed by section so one section's save state never blocks another's.
  const saving = reactive<Record<string, boolean>>({})
  const saveStatus = reactive<Record<string, SaveStatus>>({})
  const saveError = reactive<Record<string, string>>({})
  // Keyed by setting key; populated from a 422 validation failure.
  const fieldErrors = reactive<Record<string, string>>({})

  /** Fetch the settings view and apply it in place (no `loading` toggle), so the
   *  keyed section tree stays mounted — preserving focus and the Advanced
   *  accordion state across refreshes (WCAG 2.4.3). */
  async function refreshSections(): Promise<void> {
    const data = await api.get<SettingsResponse>('/settings')
    sections.value = data.sections
  }

  async function load(): Promise<void> {
    loading.value = true
    loadError.value = ''
    try {
      await refreshSections()
    } catch (err) {
      loadError.value = err instanceof Error ? err.message : 'Unknown error'
    } finally {
      loading.value = false
    }
  }

  /** Persist a section's changed keys. Returns true on success. On a 422 the
   *  offending key is recorded in ``fieldErrors``. The PUT response is the
   *  refreshed settings view, so applying it is server-truth (not optimistic). */
  async function saveSection(
    section: string,
    updates: Record<string, unknown>,
  ): Promise<boolean> {
    for (const key of Object.keys(updates)) delete fieldErrors[key]
    saving[section] = true
    saveStatus[section] = 'saving'
    saveError[section] = ''
    try {
      const data = await api.put<SettingsResponse>('/settings', { updates })
      sections.value = data.sections
      saveStatus[section] = 'saved'
      return true
    } catch (err) {
      saveStatus[section] = 'error'
      if (err instanceof ApiError && err.status === 422) {
        const invalid = extractValidationError(err.body)
        if (invalid) {
          fieldErrors[invalid.key] = invalid.reason
          saveError[section] = invalid.reason
          return false
        }
      }
      saveError[section] = err instanceof Error ? err.message : 'Unknown error'
      return false
    } finally {
      saving[section] = false
    }
  }

  function clearSaveStatus(section: string): void {
    saveStatus[section] = 'idle'
  }

  async function resetSetting(key: string): Promise<void> {
    const data = await api.delete<SettingsResponse>(
      `/settings/${encodeURIComponent(key)}`,
    )
    sections.value = data.sections
  }

  // Secrets are write-only: PUT/DELETE return 204, then refresh in place so
  // SettingsPage keeps the keyed section tree mounted (focus + Advanced accordion
  // state preserved — WCAG 2.4.3).
  async function setSecret(key: string, value: string): Promise<void> {
    await api.put('/settings/secret', { key, value })
    await refreshSections()
  }

  async function clearSecret(key: string): Promise<void> {
    await api.delete(`/settings/secret/${encodeURIComponent(key)}`)
    await refreshSections()
  }

  return {
    sections,
    loading,
    loadError,
    saving,
    saveStatus,
    saveError,
    fieldErrors,
    load,
    saveSection,
    clearSaveStatus,
    resetSetting,
    setSecret,
    clearSecret,
  }
})
