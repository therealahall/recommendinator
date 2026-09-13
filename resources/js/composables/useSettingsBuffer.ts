import { computed, reactive, ref, watch } from 'vue'
import type { SettingViewValue } from '@/types/api'

export type SettingBufferValue = string | number | boolean | string[]

function coerce(setting: SettingViewValue): SettingBufferValue {
  const rawValue = setting.value
  switch (setting.type) {
    case 'bool':
      return Boolean(rawValue)
    case 'int':
    case 'float': {
      if (typeof rawValue === 'number') return rawValue
      const parsedNumber = Number(rawValue)
      return Number.isFinite(parsedNumber) ? parsedNumber : 0
    }
    case 'list':
      return Array.isArray(rawValue) ? rawValue.map(String) : []
    default:
      return rawValue == null ? '' : String(rawValue)
  }
}

/** A save the server refused answers `false` instead of throwing. */
function refused(result: unknown): boolean {
  return result === false
}

/** Server truth lives in the store; the buffer is the editing copy, and
 *  `original` is what a key is measured against to decide it changed. */
export function useSettingsBuffer(source: () => SettingViewValue[]) {
  const buffer = reactive<Record<string, SettingBufferValue>>({})
  const original = reactive<Record<string, SettingBufferValue>>({})
  const claimed = new Set<string>()
  const inFlight = ref(0)

  // Seed a key on first sight, then leave it alone: a secret save refreshes the
  // whole section, and rereading server truth there discarded every unsaved
  // edit in it. Only the panel's own writes take a value back.
  watch(
    source,
    (settings) => {
      for (const setting of settings) {
        if (setting.key in original && !claimed.has(setting.key)) continue
        claimed.delete(setting.key)
        const coerced = coerce(setting)
        buffer[setting.key] = coerced
        original[setting.key] = coerced
      }
    },
    { immediate: true },
  )

  function release(keys: string[]): void {
    for (const key of keys) claimed.delete(key)
  }

  /** `keys` are claimed before the request because the view it answers with can
   *  reach the buffer before this resumes; a write that landed nothing — it
   *  threw, or answered `false` — hands them back. */
  async function write<T>(keys: string[], request: () => Promise<T>): Promise<T> {
    for (const key of keys) claimed.add(key)
    inFlight.value += 1
    try {
      const result = await request()
      if (refused(result)) release(keys)
      return result
    } catch (error) {
      release(keys)
      throw error
    } finally {
      inFlight.value -= 1
    }
  }

  function changed(key: string): boolean {
    return JSON.stringify(buffer[key]) !== JSON.stringify(original[key])
  }

  function changedUpdates(): Record<string, unknown> {
    const updates: Record<string, unknown> = {}
    for (const setting of source()) {
      if (changed(setting.key)) updates[setting.key] = buffer[setting.key]
    }
    return updates
  }

  const dirty = computed(() => source().some((setting) => changed(setting.key)))

  // Every write goes through `write` to claim, so a lock read from here cannot
  // be short a kind of write.
  const writing = computed(() => inFlight.value > 0)

  return { buffer, changedUpdates, dirty, write, writing }
}

export type SettingsBuffer = ReturnType<typeof useSettingsBuffer>
