import { nextTick, type Ref } from 'vue'

/** Sends focus where a decision left something to read (WCAG 2.4.3): the
 *  *refusal* it drew, else, where it unmounted the row, *preferred* if that
 *  survived and the nearest row below if not. Read after the run. */
export async function keepFocusInList(
  list: Ref<HTMLElement | null>,
  fallback: Ref<HTMLElement | null>,
  index: number,
  keys: () => string[],
  run: () => Promise<void>,
  refusal: () => HTMLElement | null = () => null,
  preferred: () => string = () => '',
): Promise<void> {
  const focused = document.activeElement
  const before = keys()
  await run()
  await nextTick()
  // Above the guards: a refusal that changed nothing leaves its control focused.
  const refused = refusal()
  if (refused !== null) {
    refused.focus()
    return
  }
  if (!(focused instanceof HTMLElement) || focused.isConnected) return
  if (document.activeElement !== null && document.activeElement !== document.body) return
  const after = keys()
  const kept = preferred()
  const next = after.includes(kept)
    ? kept
    : before.slice(index + 1).find((key) => after.includes(key))
  const target = next === undefined ? after.length - 1 : after.indexOf(next)
  const rows = list.value?.children
  const row = rows && target >= 0 ? rows[target] : undefined
  ;(row?.querySelector<HTMLElement>('button') ?? fallback.value)?.focus()
}
