import { type Ref, onMounted, onUnmounted, nextTick } from 'vue'

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Only one focus trap should be active at a time in this application.
 * This composable attaches a document-level keydown listener and does not
 * support stacked/nested traps.
 */
export function useFocusTrap(
  containerRef: Ref<HTMLElement | null>,
  onEscape: () => void,
): void {
  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      onEscape()
      return
    }
    if (event.key === 'Tab') {
      const focusable = containerRef.value?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (!focusable || focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
  }

  // Remember the element that had focus before the trap activated so we
  // can restore it on close — without this, keyboard users get stranded
  // at <body> after the modal closes (WCAG 2.4.3).
  let previousFocus: HTMLElement | null = null

  onMounted(() => {
    previousFocus = document.activeElement as HTMLElement | null
    document.addEventListener('keydown', onKeydown)
    nextTick(() => {
      containerRef.value?.focus()
    })
  })

  onUnmounted(() => {
    document.removeEventListener('keydown', onKeydown)
    if (previousFocus && document.contains(previousFocus)) {
      previousFocus.focus()
    }
  })
}
