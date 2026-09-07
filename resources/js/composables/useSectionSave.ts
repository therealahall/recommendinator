import { computed, onBeforeUnmount } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { humanizeSection } from '@/utils/format'
import type { SettingViewValue } from '@/types/api'

const SAVED_PILL_MS = 2500

export function useSectionSave(
  section: () => string,
  fields: () => SettingViewValue[],
  changedUpdates: () => Record<string, unknown>,
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
    // Guard re-entry here so the button can stay focusable (aria-disabled does
    // not block activation), which keeps focus where the user left it on the
    // success path (WCAG 2.4.3).
    if (saving.value) return null
    clearTimer()
    const updates = changedUpdates()
    // Nothing edited: don't PUT an empty object and then claim "Saved ✓", which
    // tells the user a write happened that did not.
    if (Object.keys(updates).length === 0) {
      await announce('No changes to save.')
      return null
    }
    if (await store.saveSection(section(), updates)) {
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
