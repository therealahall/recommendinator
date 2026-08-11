<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { domId } from '@/utils/format'
import type { TraktPollResponse } from '@/types/api'

// Timers are injected so Vitest can drive the poll loop without waiting real
// seconds. The defaults bind to the real window timers in the browser.
const props = withDefaults(
  defineProps<{
    sourceId: string
    sourceName: string
    // The status `enabled` flag folds a disabled source in with missing client
    // credentials, so the remedy is named by the parent, which knows which.
    connectHint: string
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
// secret resolve server-side. The status ``enabled`` flag reflects exactly
// that, so gate the connect action on it instead of surfacing the failure only
// after a click.
const canConnect = computed(() => data.oauthStatusFor(props.sourceId).enabled)
const hintId = computed(() => domId('trakt-connect-hint', props.sourceId))
// Two expanded Trakt panels render both of these, and NVDA's element list is
// name-only: without the source name neither entry says which one it drives.
const connectLabel = computed(
  () => `Connect Trakt Account for ${props.sourceName}`,
)
const resultLabel = computed(() => `${props.sourceName} connection result`)

type FlowState = 'idle' | 'starting' | 'awaiting' | 'connected' | 'error'
const state = ref<FlowState>('idle')
const userCode = ref('')
const verificationUrl = ref('')
const message = ref('')
const resultText = ref('')

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
    const flow = await data.startTraktFlow(props.sourceId)
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
    result = await data.pollTraktApproval(props.sourceId, deviceCode)
  } catch {
    state.value = 'error'
    message.value = 'Connection check failed. Try connecting again.'
    await nextTick()
    resultPanel.value?.focus()
    return
  }

  if (result.connected) {
    // Read before the state change hides the code panel: in a browser that
    // blurs its occupant to <body>, and rescuing THAT focus is the only reason
    // to move any. poll() fires from a timer, so a user who tabbed into the
    // settings while waiting must keep the field they are typing in (2.4.3).
    const rescuingFocus =
      codePanel.value?.contains(document.activeElement) ?? false
    state.value = 'connected'
    // The confirmation belongs to the panel's region, since the status flip
    // unmounts this component. That re-read is best-effort though, and when it
    // fails the parent keeps this mounted — so the panel focus lands on says
    // so itself, leaving the store's confirmation the only region speaking.
    message.value = ''
    if (!data.oauthStatusFor(props.sourceId).connected) {
      resultText.value =
        'Connected to Trakt, but the status could not be re-read. Reload the page to confirm.'
      await nextTick()
      if (rescuingFocus) resultPanel.value?.focus()
    }
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
      <!--
        aria-disabled, not disabled: a natively disabled button leaves the tab
        order, so the hint describing it is never announced to the
        screen-reader or Voice Control user it was written for. startFlow
        already refuses the activation this leaves reachable.
      -->
      <button
        ref="startButton"
        type="button"
        class="btn btn-primary"
        data-testid="trakt-connect-btn"
        :aria-disabled="!canConnect || undefined"
        :aria-label="connectLabel"
        :aria-describedby="canConnect ? undefined : hintId"
        @click="startFlow"
      >Connect Trakt Account</button>
      <p
        v-if="!canConnect"
        :id="hintId"
        class="oauth-connect-hint"
        data-testid="trakt-connect-hint"
      >{{ connectHint }}</p>
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
      data-testid="trakt-result-panel"
      role="group"
      :aria-label="resultLabel"
      tabindex="-1"
    >
      <p
        v-if="resultText"
        class="trakt-flow-result"
        data-testid="trakt-result-text"
      >{{ resultText }}</p>
      <button
        v-if="state === 'error'"
        type="button"
        class="btn btn-primary"
        data-testid="trakt-retry-btn"
        @click="retry"
      >Try Again</button>
    </div>

    <!--
      A SINGLE live region, mounted unconditionally and empty. v-show is no
      better than v-if here: display:none takes it out of the accessibility
      tree, so it would still arrive already carrying "Requesting a device
      code…" and JAWS would read that as page content, not a status change
      (WCAG 4.1.3). The prefix is conditional so the region stays :empty until
      it has something to say, and aria-atomic reads it with the message.
    -->
    <p
      class="trakt-flow-status"
      :class="{ 'trakt-flow-status--error': state === 'error' }"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    ><span v-if="message" class="sr-only">{{ sourceName }}: </span>{{ message }}</p>
  </div>
</template>

<style scoped>
/* Pointer focus only, mirroring .main-content in base.css. A keyboard-driven
   flow propagates :focus-visible through the programmatic focus() below, and
   the ring is the only thing telling that user where they now stand (2.4.7). */
.trakt-flow-panel:focus:not(:focus-visible) {
  outline: none;
}

.trakt-flow-result {
  font-size: var(--text-sm);
  color: var(--text-primary);
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

/* Collapse the box while the region has nothing to say. Not display:none —
   that is the accessibility-tree removal the region exists to avoid. */
.trakt-flow-status:empty {
  margin-top: 0;
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
