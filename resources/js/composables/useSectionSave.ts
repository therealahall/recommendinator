import { computed, onBeforeUnmount } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { humanizeSection } from '@/utils/format'
import type { SettingsBuffer } from '@/composables/useSettingsBuffer'
import type { SettingViewValue } from '@/types/api'

const SAVED_PILL_MS = 2500

export function useSectionSave(
  section: () => string,
  fields: () => SettingViewValue[],
  edits: SettingsBuffer,
  announce: (text: string) => Promise<void>,
) {
  const store = useSettingsStore()

  const saving = computed(() => store.saving[section()] ?? false)
  const saveStatus = computed(() => store.saveStatus[section()] ?? 'idle')
  const offending = computed(() => fields().find((setting) => store.fieldErrors[setting.key]))

  // The service's pattern message is deictic ("see this setting's help"), and
  // the banner sits in the section footer naming no field. Prefix the offending
  // label so the pointer has a referent from there too.
  const saveErrorText = computed(() => {
    const message = store.saveError[section()] || 'failed to save'
    return offending.value ? `${offending.value.label}: ${message}` : message
  })

  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  /** Hands back the refused setting rather than reporting it: where focus and
   *  disclosure go is the caller's to decide. */
  async function save(): Promise<SettingViewValue | null> {
    clearTimer()
    const updates = edits.changedUpdates()
    const written = Object.keys(updates)
    // Nothing edited: don't PUT an empty object and then claim "Saved ✓", which
    // tells the user a write happened that did not.
    if (written.length === 0) {
      await announce('No changes to save.')
      return null
    }
    if (await edits.write(written, () => store.saveSection(section(), updates))) {
      // Focus stays on Save, whose label reverts to what it was: without this
      // the write lands in silence.
      await announce(`${humanizeSection(section())} saved.`)
      timer = setTimeout(() => {
        store.clearSaveStatus(section())
        timer = null
      }, SAVED_PILL_MS)
      return null
    }
    return offending.value ?? null
  }

  onBeforeUnmount(clearTimer)

  return { saving, saveStatus, saveErrorText, save }
}
