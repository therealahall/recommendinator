import { ref, type Ref } from 'vue'

/** Ask before a backdrop click or Escape throws away unsaved work. */
export function useDiscardGuard(dirty: Ref<boolean>, close: () => void) {
  const confirming = ref(false)

  function requestClose() {
    if (confirming.value) confirming.value = false
    else if (dirty.value) confirming.value = true
    else close()
  }

  function keepEditing() {
    confirming.value = false
  }

  return { confirming, requestClose, keepEditing }
}
