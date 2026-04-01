import { ref, onUnmounted } from 'vue'

export function usePolling(callback: () => Promise<void>, intervalMs: number) {
  const polling = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  function start() {
    if (polling.value) return
    polling.value = true
    timer = setInterval(callback, intervalMs)
  }

  function stop() {
    polling.value = false
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { polling, start, stop }
}
