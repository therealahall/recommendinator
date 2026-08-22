import { onBeforeUnmount, reactive, ref } from 'vue'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
export const SAVED_STATUS_MS = 2500

/** One in-flight request per secret field, in the shape the config form renders.
 *  The outcome is what the row reports, so the caller's action is awaited here
 *  rather than left to reject unhandled out of an emit. */
export function useSecretStatus(onStored: () => void) {
  const status = ref<Record<string, SaveStatus>>({})
  const error = ref<Record<string, string>>({})
  const timers: Record<string, ReturnType<typeof setTimeout>> = {}

  function set(name: string, next: SaveStatus, message = ''): void {
    const running = timers[name]
    if (running) clearTimeout(running)
    delete timers[name]
    status.value = { ...status.value, [name]: next }
    error.value = { ...error.value, [name]: message }
    if (next !== 'saved') return
    timers[name] = setTimeout(() => {
      delete timers[name]
      status.value = { ...status.value, [name]: 'idle' }
    }, SAVED_STATUS_MS)
  }

  async function run(name: string, action: () => Promise<void>): Promise<void> {
    if (status.value[name] === 'saving') return
    set(name, 'saving')
    try {
      await action()
    } catch (err) {
      set(name, 'error', err instanceof Error ? err.message : 'Unknown error')
      return
    }
    set(name, 'saved')
    onStored()
  }

  onBeforeUnmount(() => {
    for (const timer of Object.values(timers)) clearTimeout(timer)
  })

  // reactive, not the refs: these are read straight into template props, where
  // a ref returned inside an object is not unwrapped.
  return reactive({ status, error, run })
}
