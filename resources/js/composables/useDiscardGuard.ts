import { nextTick, ref, type Ref } from 'vue'

export function useDiscardGuard(
  dirty: Ref<boolean>,
  close: () => void,
  container: Ref<HTMLElement | null>,
) {
  const confirming = ref(false)
  let asked: HTMLElement | null = null

  function requestClose() {
    if (confirming.value) {
      keepEditing()
    } else if (dirty.value) {
      const active = document.activeElement
      asked = active instanceof HTMLElement ? active : null
      confirming.value = true
    } else {
      close()
    }
  }

  // The confirmation unmounts under the focus it took, so declining has to put
  // focus back in the dialog or the next Tab walks out of it (WCAG 2.4.3).
  async function keepEditing() {
    confirming.value = false
    await nextTick()
    const back = asked !== null && container.value?.contains(asked) ? asked : container.value
    back?.focus()
  }

  return { confirming, requestClose, keepEditing }
}
