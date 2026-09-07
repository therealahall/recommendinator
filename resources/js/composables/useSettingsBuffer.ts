import { reactive, watch } from 'vue'
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

/** Server truth lives in the store; the buffer is the editing copy, and
 *  `original` is what a key is measured against to decide it changed. */
export function useSettingsBuffer(source: () => SettingViewValue[]) {
  const buffer = reactive<Record<string, SettingBufferValue>>({})
  const original = reactive<Record<string, SettingBufferValue>>({})

  watch(
    source,
    (settings) => {
      for (const setting of settings) {
        const coerced = coerce(setting)
        buffer[setting.key] = coerced
        original[setting.key] = coerced
      }
    },
    { immediate: true, deep: true },
  )

  function changedUpdates(): Record<string, unknown> {
    const updates: Record<string, unknown> = {}
    for (const setting of source()) {
      const key = setting.key
      if (JSON.stringify(buffer[key]) !== JSON.stringify(original[key])) {
        updates[key] = buffer[key]
      }
    }
    return updates
  }

  return { buffer, changedUpdates }
}
