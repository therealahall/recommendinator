<script setup lang="ts">
import { computed, nextTick, onMounted, ref, toRef, watch } from 'vue'
import DeviceCodeFlow from '@/components/molecules/DeviceCodeFlow.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import { RECHECKING_STATUS, useOAuthGate } from '@/composables/useOAuthGate'
import { useDataStore } from '@/stores/data'
import type { OAuthConnectSchema } from '@/types/api'

const props = defineProps<{
  sourceId: string
  sourceName: string
  plugin: string
  /** What the plugin declares, null for one with no account to connect. */
  oauth: OAuthConnectSchema | null
  serviceName: string
  /** The source's own enabled flag, which is half of what gates connecting. */
  sourceEnabled: boolean
  disabled: boolean
  expanded: boolean
  /** Bumped by any write that can move the gate, so the panel re-reads it. */
  gateRevision: number
}>()

const data = useDataStore()

const oauthPlugin = computed(() => (props.oauth ? props.plugin : null))
const gate = useOAuthGate(toRef(props, 'sourceId'), oauthPlugin)

onMounted(gate.reload)
watch(() => props.gateRevision, gate.refresh)

// Accordion.vue hides the body with `hidden` rather than unmounting it, so a
// message left from the last visit would re-enter the accessibility tree
// already populated — read as page content, never as a status (WCAG 4.1.3).
watch(
  () => props.expanded,
  (open) => {
    if (open) data.setOAuthMessage(props.sourceId, '')
  },
)

// Named for the source, not just the service: two expanded panels of one
// service would otherwise offer two buttons with the identical accessible name.
const disconnectLabel = computed(
  () => `Disconnect ${props.sourceName} from ${props.serviceName}`,
)
const panelLabel = computed(() => `${props.sourceName} connection`)
const connectedLabel = computed(() => `${props.serviceName} account connected.`)
const status = computed(() => data.oauthStatusFor(props.sourceId))
const message = computed(() => data.oauthMessages[props.sourceId] ?? '')
const connectable = computed(() => !gate.failed.value && !status.value.connected)
// Not gated on the auth URL: a source the server will not connect gets one
// disabled button and a hint naming the remedy, where dropping the whole block
// left a named, empty group announcing nothing.
const showCodeConnect = computed(
  () => connectable.value && props.oauth?.flow === 'code_paste',
)
const showDeviceConnect = computed(
  () => connectable.value && props.oauth?.flow === 'device_code',
)
// Neither flow can name its own remedy: a device-code `enabled` folds "disabled"
// in with unsaved setup, and a code-paste auth URL is null when its builder
// throws while enabled. Only the enable flag tells those apart.
const connectHint = computed(() => {
  // Mid-refresh the two halves disagree, the settings one having moved first.
  if (gate.refreshing.value) return RECHECKING_STATUS
  if (!props.sourceEnabled) {
    return 'Enable this source in the settings below before you can connect.'
  }
  if (props.oauth?.setup_hint) return props.oauth.setup_hint
  return 'The service did not return a sign-in link. Try again in a moment.'
})
// An unreadable status asserts nothing: claiming a connection next to "could
// not read the status" leaves no way to tell which statement is current.
const showConnected = computed(() => !gate.failed.value && status.value.connected)

const panel = ref<HTMLElement | null>(null)
const retrying = ref(false)
const disconnecting = ref(false)
// Tracks the visible label, which speech-input users say back (WCAG 2.5.3).
const retryStatusLabel = computed(
  () =>
    `${retrying.value ? 'Retrying' : 'Retry'} the connection status ` +
    `check for ${props.sourceName}`,
)

// Each outcome can take the control holding focus with it, dropping the
// keyboard user to <body> (WCAG 2.4.3). Keyed on whether that element actually
// went away: a refused disconnect leaves its button mounted.
watch([() => status.value.connected, gate.failed], () => {
  const focused = document.activeElement
  void nextTick(() => {
    if (!(focused instanceof HTMLElement) || focused.isConnected) return
    panel.value?.focus()
  })
})

// Only the status re-read rejects out of these: a refused connect or disconnect
// is reported in the live region instead. The server has already acted by then,
// so showing the status as unknown puts one statement on screen.
async function onDisconnect(): Promise<void> {
  if (props.disabled || disconnecting.value || !oauthPlugin.value) return
  disconnecting.value = true
  try {
    await data.disconnectOAuth(
      props.sourceId,
      oauthPlugin.value,
      `Disconnecting ${props.serviceName}...`,
    )
  } catch {
    gate.failed.value = true
  } finally {
    disconnecting.value = false
  }
}

async function onSubmitCode(code: string): Promise<void> {
  if (!oauthPlugin.value) return
  try {
    await data.submitOAuthCode(
      props.sourceId,
      oauthPlugin.value,
      code,
      `Connecting to ${props.serviceName}...`,
    )
  } catch {
    gate.failed.value = true
  }
}

async function onRetryStatus(): Promise<void> {
  if (retrying.value) return
  retrying.value = true
  data.setOAuthMessage(props.sourceId, RECHECKING_STATUS)
  await gate.reload()
  retrying.value = false
  // A second failure changes nothing else on screen.
  gate.say('Still could not read the connection status. Try again in a moment.')
}
</script>

<template>
  <template v-if="oauth">
    <!--
      Rendered whatever the connection state: it is the focus target when an
      outcome removes the button that had focus, and an empty group would give
      a sighted keyboard user nothing to read the announcement against.
    -->
    <div
      ref="panel"
      class="source-connect"
      role="group"
      :aria-label="panelLabel"
      tabindex="-1"
    >
      <p
        v-if="showConnected"
        class="source-connect-connected"
        data-testid="oauth-connected"
      >{{ connectedLabel }}</p>

      <button
        v-if="showConnected"
        type="button"
        class="btn btn-danger"
        :data-testid="`disconnect-btn-${sourceId}`"
        :aria-label="disconnectLabel"
        :aria-disabled="disabled || disconnecting || undefined"
        @click="onDisconnect"
      >Disconnect</button>

      <OAuthConnectFlow
        v-if="showCodeConnect"
        :source-id="sourceId"
        :source-name="sourceName"
        :auth-url="status.authUrl"
        :help-text="oauth.code_help"
        :service-name="serviceName"
        :connect-hint="connectHint"
        @submit="onSubmitCode"
      />

      <DeviceCodeFlow
        v-if="showDeviceConnect"
        :source-id="sourceId"
        :source-name="sourceName"
        :plugin="plugin"
        :service-name="serviceName"
        :connect-hint="connectHint"
      />

      <!--
        Plain content, not role="alert": it can only appear as the body first
        renders, where an alert arrives already populated and is read as page
        content. The retry outcome goes to the region below.
      -->
      <div v-if="gate.failed.value" class="state state--error">
        <p data-testid="oauth-status-error">
          Could not read this source's connection status.
        </p>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="oauth-status-retry"
          :aria-label="retryStatusLabel"
          :aria-disabled="retrying || undefined"
          @click="onRetryStatus"
        >{{ retrying ? 'Retrying…' : 'Retry' }}</button>
      </div>
    </div>

    <!--
      The one live region for the whole OAuth lifecycle. Visible: a refused
      disconnect changes nothing else on screen. Outside the focus target, so
      landing there does not repeat it. Named, since several panels announce
      and nothing collapses the others.
    -->
    <p
      class="source-connect-message"
      data-testid="oauth-message"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    ><span v-if="message" class="sr-only">{{ sourceName }}: </span>{{ message }}</p>
  </template>
</template>

<style scoped>
.source-connect {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

/* The panel is focused programmatically, which propagates :focus-visible from
   the control the user just activated — so a keyboard disconnect keeps the ring
   that says where focus went (2.4.7), and a mouse one never draws it. */
.source-connect:focus:not(:focus-visible) {
  outline: none;
}

.source-connect-connected {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.source-connect-message {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

/* Silent, the region still has to stay in the accessibility tree, so it earns
   its spacing only once it says something. */
.source-connect-message:not(:empty) {
  margin-bottom: var(--space-3);
}
</style>
