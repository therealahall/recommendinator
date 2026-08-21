import { nextTick, type Ref } from 'vue'

/** Keeps a keyboard user off <body> when a decision unmounts the row they had
 *  (WCAG 2.4.3): *preferred* where it survived, else the nearest row below.
 *  Read after the run, since a part-way failure decides what survives. */
export async function keepFocusInList(
  list: Ref<HTMLElement | null>,
  fallback: Ref<HTMLElement | null>,
  index: number,
  keys: () => string[],
  run: () => Promise<void>,
  preferred: () => string = () => '',
): Promise<void> {
  const focused = document.activeElement
  const before = keys()
  await run()
  await nextTick()
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
