import { reactive, ref } from 'vue'

/** One form's request in flight, in the shape the auth and account forms render.
 *  The action answers with the message to show, or '' when it worked. */
export function useSubmission() {
  const pending = ref(false)
  const error = ref('')
  const saved = ref(false)

  async function submit(action: () => Promise<string>): Promise<void> {
    // The form's own guard reads a prop, which lags this ref by a render, so a
    // second Enter within the round trip arrives while the first is still open.
    if (pending.value) return

    pending.value = true
    error.value = ''
    saved.value = false
    try {
      error.value = await action()
      saved.value = error.value === ''
    } finally {
      pending.value = false
    }
  }

  // reactive, not the refs: these are read straight into template props, where a
  // ref returned inside an object is not unwrapped.
  return reactive({ pending, error, saved, submit })
}
