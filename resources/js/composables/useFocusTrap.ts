import { type Ref, onMounted, onUnmounted, nextTick } from 'vue'

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary:not([hidden]), a[href], [tabindex]:not([tabindex="-1"])'

// The selector only filters the HTML `hidden` attribute, but `v-show` hides an
// element with inline `display: none`, which the browser also skips when
// tabbing. Including one would make it a phantom first/last stop and break
// wrapping, so drop anything hidden on the element ITSELF. This does not see an
// element hidden by an ancestor — `getComputedStyle(child).display` reports the
// child's own value, not the ancestor's `none` — which is fine for the dialogs
// using this trap, where `v-show` sits on the focusable's own node.
function isVisible(element: HTMLElement): boolean {
  const style = getComputedStyle(element)
  return style.display !== 'none' && style.visibility !== 'hidden'
}

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
      const container = containerRef.value
      if (!container) return
      const focusable = [
        ...container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ].filter(isVisible)
      const active = document.activeElement as HTMLElement | null
      const index = active ? focusable.indexOf(active) : -1
      const lastIndex = focusable.length - 1
      // Focus is not on anything the trap owns: it is on the container itself
      // (tabindex="-1", so never in `focusable`) after mount or a click on the
      // dialog's text, on <body> after a control blurred mid-request, or on a
      // control behind the overlay. Native Tab would walk to whatever sits
      // next in document order, which is outside an aria-modal dialog and so
      // invisible to the screen reader's virtual cursor. Enter the trap at the
      // near edge instead, falling back to the container when it is
      // momentarily empty of focusables (WCAG 2.4.3).
      if (index === -1) {
        event.preventDefault()
        ;(focusable[event.shiftKey ? lastIndex : 0] ?? container).focus()
      } else if (event.shiftKey && index === 0) {
        event.preventDefault()
        focusable[lastIndex].focus()
      } else if (!event.shiftKey && index === lastIndex) {
        event.preventDefault()
        focusable[0].focus()
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
