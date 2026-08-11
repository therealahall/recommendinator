<script setup lang="ts">
import { ref, computed } from 'vue'
import { domId } from '@/utils/format'

const props = defineProps<{
  sourceId: string
  authUrl: string | null
  expectedOrigin: string
  connectMessage: string
  helpText: string
  serviceName: string
}>()

const emit = defineEmits<{
  submit: [code: string]
}>()

const codeInput = ref('')
const showCodeStep = ref(false)
const codeInputId = computed(() => domId('oauth-code', props.sourceId))

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
    <button class="btn btn-primary" @click="openAuth">Connect {{ serviceName }}</button>
    <div v-if="showCodeStep">
      <p class="help-text my-2">{{ helpText }}</p>
      <div class="oauth-input-row">
        <label :for="codeInputId" class="sr-only">{{ serviceName }} authorization code</label>
        <input :id="codeInputId" type="text" v-model="codeInput" placeholder="Paste authorization code...">
        <button class="btn btn-primary" @click="submitCode">Connect</button>
      </div>
      <!--
        Deliberately NOT a live region: this component unmounts the moment the
        connect succeeds, so the panel owns the region that announces it. Two
        regions carrying the same words announce twice.
      -->
      <div class="mt-2">{{ connectMessage }}</div>
    </div>
  </div>
</template>
