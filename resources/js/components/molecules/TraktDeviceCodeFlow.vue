<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import type { TraktPollResponse } from '@/types/api'

// Timers are injected so Vitest can drive the poll loop without waiting real
// seconds. The defaults bind to the real window timers in the browser.
const props = withDefaults(
  defineProps<{
    setTimer?: (handler: () => void, delayMs: number) => number
    clearTimer?: (handle: number) => void
  }>(),
  {
    setTimer: (handler: () => void, delayMs: number) =>
      window.setTimeout(handler, delayMs),
    clearTimer: (handle: number) => window.clearTimeout(handle),
  },
)

const data = useDataStore()

// The device-flow POST returns 400 until both the Trakt client ID and client
// secret resolve server-side. traktStatus.enabled reflects exactly that, so
// gate the connect action on it instead of surfacing the failure only after a
// click.
const canConnect = computed(() => data.traktStatus.enabled)

type FlowState = 'idle' | 'starting' | 'awaiting' | 'connected' | 'error'
const state = ref<FlowState>('idle')
const userCode = ref('')
const verificationUrl = ref('')
const message = ref('')

let deviceCode = ''
let intervalMs = 5000
let pollHandle: number | null = null

const startButton = ref<HTMLButtonElement | null>(null)
const codePanel = ref<HTMLElement | null>(null)
const resultPanel = ref<HTMLElement | null>(null)

function clearPoll(): void {
  if (pollHandle !== null) {
    props.clearTimer(pollHandle)
    pollHandle = null
  }
}

async function startFlow(): Promise<void> {
  if (!canConnect.value) return
  state.value = 'starting'
  message.value = 'Requesting a device code from Trakt…'
  try {
    const flow = await data.startTraktFlow()
    deviceCode = flow.device_code
    userCode.value = flow.user_code
    verificationUrl.value = flow.verification_url
    intervalMs = Math.max(1, flow.interval) * 1000
    state.value = 'awaiting'
    message.value = 'Waiting for you to approve the code on Trakt…'
    await nextTick()
    codePanel.value?.focus()
    schedulePoll(intervalMs)
  } catch {
    state.value = 'error'
    message.value =
      'Could not start the Trakt connection. Check that the Trakt client ' +
      'credentials are configured, then try again.'
    await nextTick()
    resultPanel.value?.focus()
  }
}

function schedulePoll(delayMs: number): void {
  clearPoll()
  pollHandle = props.setTimer(() => {
    pollHandle = null
    void poll()
  }, delayMs)
}

async function poll(): Promise<void> {
  let result: TraktPollResponse
  try {
    result = await data.pollTraktApproval(deviceCode)
  } catch {
    state.value = 'error'
    message.value = 'Connection check failed. Try connecting again.'
    await nextTick()
    resultPanel.value?.focus()
    return
  }

  if (result.connected) {
    state.value = 'connected'
    message.value = result.message || 'Trakt account connected.'
    await nextTick()
    resultPanel.value?.focus()
    return
  }

  switch (result.status) {
    case 'slow_down':
      intervalMs += 5000
      message.value = 'Trakt asked us to slow down — still waiting for approval…'
      schedulePoll(intervalMs)
      break
    case 'expired':
      state.value = 'error'
      message.value = 'The code expired before it was approved. Try again.'
      await nextTick()
      resultPanel.value?.focus()
      break
    case 'denied':
      state.value = 'error'
      message.value = 'The connection was denied on Trakt. Try again.'
      await nextTick()
      resultPanel.value?.focus()
      break
    default:
      message.value = 'Waiting for you to approve the code on Trakt…'
      schedulePoll(intervalMs)
  }
}

async function retry(): Promise<void> {
  clearPoll()
  await startFlow()
}

onBeforeUnmount(clearPoll)
</script>

<template>
  <div class="trakt-flow">
    <template v-if="state === 'idle'">
      <button
        ref="startButton"
        type="button"
        class="btn btn-primary trakt-flow-connect"
        data-testid="trakt-connect-btn"
        :disabled="!canConnect"
        :aria-describedby="canConnect ? undefined : 'trakt-connect-hint'"
        @click="startFlow"
      >Connect Trakt Account</button>
      <p
        v-if="!canConnect"
        id="trakt-connect-hint"
        class="trakt-flow-hint"
        data-testid="trakt-connect-hint"
      >Add the Trakt client ID and client secret in the settings below before you can connect.</p>
    </template>

    <div
      v-show="state === 'awaiting'"
      ref="codePanel"
      class="trakt-flow-panel"
      tabindex="-1"
    >
      <p class="trakt-flow-instructions">
        Go to
        <a
          :href="verificationUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="trakt-flow-link"
          data-testid="trakt-verification-link"
        >{{ verificationUrl }}<span class="sr-only"> (opens in new tab)</span></a>
        and enter this code:
      </p>
      <p class="trakt-flow-code" data-testid="trakt-user-code">
        <span class="sr-only">Your Trakt activation code is </span>
        <span class="trakt-flow-code-value">{{ userCode }}</span>
      </p>
    </div>

    <div
      v-show="state === 'connected' || state === 'error'"
      ref="resultPanel"
      class="trakt-flow-panel"
      tabindex="-1"
    >
      <button
        v-if="state === 'error'"
        type="button"
        class="btn btn-primary"
        data-testid="trakt-retry-btn"
        @click="retry"
      >Try Again</button>
    </div>

    <!--
      A SINGLE live region stays mounted across every non-idle state (via
      v-show, never v-if) so screen readers — JAWS in particular — announce
      each `message` update as a status change rather than skipping it as a
      fresh insertion (WCAG 4.1.3 status messages). Re-creating the region
      per state, or mounting it with content already populated, is the exact
      pitfall this avoids.
    -->
    <p
      v-show="state !== 'idle'"
      class="trakt-flow-status"
      :class="{ 'trakt-flow-status--error': state === 'error' }"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ message }}</p>
  </div>
</template>

<style scoped>
/* Disabled connect button: convey "unavailable" via reduced emphasis + the
   not-allowed cursor. The native ``disabled`` attribute announces the state to
   assistive tech and the adjacent hint states why, so colour is never the sole
   signal (WCAG 1.4.1). */
.trakt-flow-connect:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.trakt-flow-hint {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.trakt-flow-panel:focus {
  outline: none;
}

.trakt-flow-instructions {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin-bottom: var(--space-2);
}

.trakt-flow-link {
  color: var(--accent-light);
  text-decoration: underline;
}

.trakt-flow-code {
  margin-bottom: var(--space-2);
}

.trakt-flow-code-value {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: var(--text-2xl);
  letter-spacing: 0.15em;
  font-weight: 600;
  color: var(--text-primary);
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
}

.trakt-flow-status {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* Error state: keep the readable text in --text-primary and convey "error"
   via an error-tinted background + border (mirrors .sync-status-error in
   base.css, but with --text-primary text so it clears WCAG 1.4.3 4.5:1 —
   --color-error text on the card background only reaches ~2.5:1). The
   message text already states the error, so colour is not the sole signal. */
.trakt-flow-status--error {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-error) 35%, transparent);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2);
}
</style>
