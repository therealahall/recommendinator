import { nextTick, ref } from 'vue'

/** A persistent role="status" region only fires the screen reader when its text
 *  changes, so two identical outcomes in a row would be announced once. Blanking
 *  first forces the mutation. */
export function useAnnouncer() {
  const message = ref('')

  async function announce(text: string): Promise<void> {
    message.value = ''
    await nextTick()
    message.value = text
  }

  /** Never rejects: a refused action that changed nothing on screen is only
   *  reported through the region, so a caller must not have to guard it. */
  async function report(
    action: () => Promise<void>,
    done: string,
    failed: string,
  ): Promise<void> {
    try {
      await action()
      await announce(done)
    } catch (error) {
      await announce(error instanceof Error ? `${failed} ${error.message}` : failed)
    }
  }

  return { message, announce, report }
}
