import { nextTick, type Ref } from 'vue'

/** True when focus fell to <body>, the only state a rescue may move it out of. */
export function focusStranded(): boolean {
  const active = document.activeElement
  return active === null || active === document.body
}

/** Only out of <body>: a decision landing after the press — a watcher, a
 *  request, a timer — rescues focus, never takes it. Its refusal is a live
 *  region, and reads wherever focus sits. */
export function rescueFocus(target: HTMLElement | null | undefined): void {
  if (focusStranded()) target?.focus()
}

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
  if (!focusStranded()) return
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
