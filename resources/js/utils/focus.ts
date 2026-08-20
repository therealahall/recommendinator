import { nextTick, type Ref } from 'vue'

/** A decision unmounts the row holding focus, dropping a keyboard user to
 *  <body> (WCAG 2.4.3). Focus goes to the row that took its place, never back
 *  to the first: skipped rows stay, so the top is far behind. */
export async function keepFocusInList(
  list: Ref<HTMLElement | null>,
  fallback: Ref<HTMLElement | null>,
  index: number,
  run: Promise<void>,
): Promise<void> {
  const focused = document.activeElement
  await run
  await nextTick()
  // Someone who tabbed away mid-request keeps their own place.
  if (!(focused instanceof HTMLElement) || focused.isConnected) return
  if (document.activeElement !== null && document.activeElement !== document.body) return
  const rows = list.value?.children
  const row = rows?.length ? rows[Math.min(index, rows.length - 1)] : undefined
  ;(row?.querySelector<HTMLElement>('button') ?? fallback.value)?.focus()
}
