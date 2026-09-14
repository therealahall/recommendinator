<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { focusStranded } from '@/utils/focus'
import { domId } from '@/utils/format'
import type { DevicePollResponse } from '@/types/api'

// Timers are injected so Vitest can drive the poll loop without waiting real
// seconds. The defaults bind to the real window timers in the browser.
const props = withDefaults(
  defineProps<{
    sourceId: string
    sourceName: string
    plugin: string
    serviceName: string
    // The status `enabled` flag folds a disabled source in with unsaved setup,
    // so the remedy is named by the parent, which knows which.
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

// The device-flow POST refuses until the source's setup resolves server-side,
// which is exactly what the status `enabled` flag reflects, so gate the connect
// action on it instead of surfacing the failure only after a click.
const canConnect = computed(() => data.oauthStatusFor(props.sourceId).enabled)
const hintId = computed(() => domId('device-connect-hint', props.sourceId))
// Two expanded panels of one service render both of these, and NVDA's element
// list is name-only: without the source name neither says which one it drives.
const connectLabel = computed(
  () => `Connect ${props.serviceName} Account for ${props.sourceName}`,
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

const codePanel = ref<HTMLElement | null>(null)
const resultPanel = ref<HTMLElement | null>(null)

/** True when nobody holds focus: it is at <body>, or in the code panel the
 *  caller is about to hide. The poll lands from a timer, so a user who tabbed
 *  away meanwhile keeps their field (2.4.3). */
function focusRescuable(): boolean {
  return focusStranded() || (codePanel.value?.contains(document.activeElement) ?? false)
}

async function failFlow(said: string): Promise<void> {
  const rescuing = focusRescuable()
  state.value = 'error'
  message.value = said
  await nextTick()
  if (rescuing) resultPanel.value?.focus()
}

function clearPoll(): void {
  if (pollHandle !== null) {
    props.clearTimer(pollHandle)
    pollHandle = null
  }
}

async function startFlow(): Promise<void> {
  if (!canConnect.value) return
  state.value = 'starting'
  message.value = `Requesting a device code from ${props.serviceName}…`
  try {
    const flow = await data.startDeviceFlow(props.sourceId, props.plugin)
    deviceCode = flow.device_code
    userCode.value = flow.user_code
    verificationUrl.value = flow.verification_url
    intervalMs = Math.max(1, flow.interval) * 1000
    state.value = 'awaiting'
    message.value = `Waiting for you to approve the code on ${props.serviceName}…`
    await nextTick()
    codePanel.value?.focus()
    schedulePoll(intervalMs)
  } catch {
    await failFlow(
      `Could not start the ${props.serviceName} connection. Check this ` +
        "source's settings below, then try again.",
    )
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
  let result: DevicePollResponse
  try {
    result = await data.pollDeviceApproval(props.sourceId, props.plugin, deviceCode)
  } catch {
    await failFlow('Connection check failed. Try connecting again.')
    return
  }

  if (result.connected) {
    // Read before the state change hides the code panel: in a browser that
    // blurs its occupant to <body>, too late to tell it from a user's own.
    const rescuingFocus = focusRescuable()
    state.value = 'connected'
    // The confirmation belongs to the panel's region, since the status flip
    // unmounts this component.
    message.value = ''
    // That re-read is best-effort though, and when it fails the parent keeps
    // this mounted — so the panel focus lands on says so itself, leaving the
    // store's confirmation the only region speaking.
    if (!data.oauthStatusFor(props.sourceId).connected) {
      resultText.value =
        `Connected to ${props.serviceName}, but the status could not be ` +
        're-read. Reload the page to confirm.'
      await nextTick()
      if (rescuingFocus) resultPanel.value?.focus()
    }
    return
  }

  switch (result.status) {
    case 'slow_down':
      intervalMs += 5000
      message.value =
        `${props.serviceName} asked us to slow down — still waiting for approval…`
      schedulePoll(intervalMs)
      break
    case 'expired':
      await failFlow('The code expired before it was approved. Try again.')
      break
    case 'denied':
      await failFlow(`The connection was denied on ${props.serviceName}. Try again.`)
      break
    default:
      message.value = `Waiting for you to approve the code on ${props.serviceName}…`
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
  <div class="device-flow">
    <template v-if="state === 'idle'">
      <!--
        aria-disabled, not disabled: a natively disabled button leaves the tab
        order, so the hint describing it is never announced to the
        screen-reader or Voice Control user it was written for. startFlow
        already refuses the activation this leaves reachable.
      -->
      <button
        type="button"
        class="btn btn-primary"
        data-testid="device-connect-btn"
        :aria-disabled="!canConnect || undefined"
        :aria-label="connectLabel"
        :aria-describedby="canConnect ? undefined : hintId"
        @click="startFlow"
      >Connect {{ serviceName }} Account</button>
      <p
        v-if="!canConnect"
        :id="hintId"
        class="oauth-connect-hint"
        data-testid="device-connect-hint"
      >{{ connectHint }}</p>
    </template>

    <div
      v-show="state === 'awaiting'"
      ref="codePanel"
      class="device-flow-panel"
      tabindex="-1"
    >
      <p class="device-flow-instructions">
        Go to
        <a
          :href="verificationUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="device-flow-link"
          data-testid="device-verification-link"
        >{{ verificationUrl }}<span class="sr-only"> (opens in new tab)</span></a>
        and enter this code:
      </p>
      <p class="device-flow-code" data-testid="device-user-code">
        <span class="sr-only">Your {{ serviceName }} activation code is </span>
        <span class="device-flow-code-value">{{ userCode }}</span>
      </p>
    </div>

    <div
      v-show="state === 'connected' || state === 'error'"
      ref="resultPanel"
      class="device-flow-panel"
      data-testid="device-result-panel"
      role="group"
      :aria-label="resultLabel"
      tabindex="-1"
    >
      <p
        v-if="resultText"
        class="device-flow-result"
        data-testid="device-result-text"
      >{{ resultText }}</p>
      <button
        v-if="state === 'error'"
        type="button"
        class="btn btn-primary"
        data-testid="device-retry-btn"
        @click="retry"
      >Try Again</button>
    </div>

    <!-- v-show is no better than v-if here: display:none takes it out of the
         accessibility tree, so it would still arrive already carrying
         "Requesting a device code…" and JAWS would read that as page content,
         not a status change (WCAG 4.1.3). -->
    <!-- The prefix is conditional so the region stays :empty until it has
         something to say, and aria-atomic reads it with the message. -->
    <p
      class="device-flow-status"
      :class="{ 'device-flow-status--error': state === 'error' }"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    ><span v-if="message" class="sr-only">{{ sourceName }}: </span>{{ message }}</p>
  </div>
</template>

<style scoped>
/* Pointer focus only, mirroring .app-stage in base.css. A keyboard-driven
   flow propagates :focus-visible through the programmatic focus() below, and
   the ring is the only thing telling that user where they now stand (2.4.7). */
.device-flow-panel:focus:not(:focus-visible) {
  outline: none;
}

.device-flow-result {
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.device-flow-instructions {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  margin-bottom: var(--space-2);
}

.device-flow-link {
  color: var(--text-primary);
  text-decoration: underline;
}

.device-flow-code {
  margin-bottom: var(--space-2);
}

.device-flow-code-value {
  display: inline-block;
  font-size: var(--text-2xl);
  font-variant-numeric: tabular-nums;
  letter-spacing: var(--tracking-widest);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
}

.device-flow-status {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* Collapse the box while the region has nothing to say. Not display:none —
   that is the accessibility-tree removal the region exists to avoid. */
.device-flow-status:empty {
  margin-top: 0;
}

/* --color-error on this card falls short as text, so the readable text stays
   --text-primary (WCAG 1.4.3) and the tint carries "error"; the message already
   states it, so colour is not the sole signal. */
.device-flow-status--error {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--color-error) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-error) 35%, transparent);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2);
}
</style>
