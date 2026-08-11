<script setup lang="ts">
import { ref, computed } from 'vue'
import { domId } from '@/utils/format'

const props = defineProps<{
  sourceId: string
  sourceName: string
  authUrl: string | null
  expectedOrigin: string
  helpText: string
  serviceName: string
  // Why the button is dead is the parent's to say: a null auth URL means the
  // source is disabled or the service refused a link, and only the parent
  // holds the enable flag that separates them.
  connectHint: string
}>()

const emit = defineEmits<{
  submit: [code: string]
}>()

const codeInput = ref('')
const showCodeStep = ref(false)
const codeInputId = computed(() => domId('oauth-code', props.sourceId))
// The server withholds the auth URL for a source it will not connect, so gate
// the action on it rather than rendering a button that silently does nothing —
// or, worse, nothing at all inside the panel's named group.
const canConnect = computed(() => props.authUrl !== null)
const hintId = computed(() => domId('oauth-connect-hint', props.sourceId))
// Two expanded panels of one plugin render each of these twice. The enclosing
// group disambiguates them on Tab, but NVDA's element list is name-only, so
// the source name has to be in the name itself.
const connectLabel = computed(
  () => `Connect ${props.serviceName} for ${props.sourceName}`,
)
const submitLabel = computed(
  () => `Connect ${props.sourceName} with the pasted code`,
)
const codeLabel = computed(
  () => `${props.serviceName} authorization code for ${props.sourceName}`,
)

function openAuth() {
  if (!props.authUrl) return
  let parsed: URL
  try {
    parsed = new URL(props.authUrl)
  } catch {
    return
  }
  if (parsed.protocol !== 'https:') {
    console.error(`Unexpected protocol in ${props.serviceName} auth URL:`, parsed.protocol)
    return
  }
  if (parsed.origin !== props.expectedOrigin) {
    console.error(`Unexpected ${props.serviceName} auth URL origin:`, parsed.origin)
    return
  }
  window.open(parsed.href, '_blank', 'noopener,noreferrer')
  showCodeStep.value = true
}

function submitCode() {
  const trimmed = codeInput.value.trim()
  if (trimmed) {
    emit('submit', trimmed)
    codeInput.value = ''
  }
}
</script>

<template>
  <div>
    <button
      class="btn btn-primary"
      :disabled="!canConnect"
      :aria-label="connectLabel"
      :aria-describedby="canConnect ? undefined : hintId"
      @click="openAuth"
    >Connect {{ serviceName }}</button>
    <p
      v-if="!canConnect"
      :id="hintId"
      class="oauth-connect-hint"
      data-testid="oauth-connect-hint"
    >{{ connectHint }}</p>
    <div v-if="showCodeStep">
      <p class="help-text my-2">{{ helpText }}</p>
      <div class="oauth-input-row">
        <label :for="codeInputId" class="sr-only">{{ codeLabel }}</label>
        <input :id="codeInputId" type="text" v-model="codeInput" placeholder="Paste authorization code...">
        <button class="btn btn-primary" :aria-label="submitLabel" @click="submitCode">Connect</button>
      </div>
      <!--
        No message rendered here: the panel's region is the one that survives
        the connect, and a second copy of the same words would be read twice.
      -->
    </div>
  </div>
</template>
