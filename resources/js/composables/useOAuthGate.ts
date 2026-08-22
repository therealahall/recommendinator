import { ref, type Ref } from 'vue'
import { useDataStore } from '@/stores/data'

/** Retry and a gate-changing write run the same re-read, so they say the same
 *  words for it. */
export const RECHECKING_STATUS = 'Rechecking the connection status…'
const STATUS_UPDATED = 'Connection status updated.'

/** Reading a source's OAuth connection status, and saying what the read found.
 *  `active` is false for a plugin with no connect flow, where every verb here
 *  is a no-op. */
export function useOAuthGate(
  sourceId: Ref<string>,
  plugin: Ref<string>,
  active: Ref<boolean>,
) {
  const data = useDataStore()
  const failed = ref(false)
  const refreshing = ref(false)
  let generation = 0

  // Tracked, not swallowed: the fallback reads as "not connected", which offers
  // a Connect button and a hint naming a remedy unrelated to the failure.
  async function reload(): Promise<void> {
    if (!active.value) return
    try {
      await data.loadOAuthStatus(sourceId.value, plugin.value)
      failed.value = false
    } catch {
      failed.value = true
    }
  }

  // Only the settings half of the gate moves when the user enables the source
  // or stores a client credential, so without this the Connect button stays
  // dead under a hint that has moved on to naming a different remedy.
  async function refresh(): Promise<void> {
    if (!active.value) return
    const mine = ++generation
    refreshing.value = true
    data.setOAuthMessage(sourceId.value, RECHECKING_STATUS)
    await reload()
    // Two rechecks overlap, the secret verbs staying live through an enable.
    // Releasing the hold on an overtaken one is how the stale remedy returns.
    if (mine !== generation) return
    refreshing.value = false
    say('Could not read the connection status. Try again in a moment.')
  }

  function say(onFailure: string): void {
    data.setOAuthMessage(sourceId.value, failed.value ? onFailure : STATUS_UPDATED)
  }

  return { failed, refreshing, reload, refresh, say }
}
