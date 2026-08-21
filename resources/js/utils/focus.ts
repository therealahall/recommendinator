import { nextTick, type Ref } from 'vue'

/** Keeps a keyboard user off <body> when a decision unmounts the row they had
 *  (WCAG 2.4.3): *preferred* where it survived, else the nearest row below by
 *  key, since one merge drops several. Someone who tabbed away keeps theirs. */
export async function keepFocusInList(
  list: Ref<HTMLElement | null>,
  fallback: Ref<HTMLElement | null>,
  index: number,
  keys: () => string[],
  run: () => Promise<void>,
  preferred = '',
): Promise<void> {
  const focused = document.activeElement
  const before = keys()
  await run()
  await nextTick()
  if (!(focused instanceof HTMLElement) || focused.isConnected) return
  if (document.activeElement !== null && document.activeElement !== document.body) return
  const after = keys()
  const next = after.includes(preferred)
    ? preferred
    : before.slice(index + 1).find((key) => after.includes(key))
  const target = next === undefined ? after.length - 1 : after.indexOf(next)
  const rows = list.value?.children
  const row = rows && target >= 0 ? rows[target] : undefined
  ;(row?.querySelector<HTMLElement>('button') ?? fallback.value)?.focus()
}
