<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
}>()

const text = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

function onInput() {
  if (textarea.value) {
    textarea.value.style.height = 'auto'
    textarea.value.style.height = Math.min(textarea.value.scrollHeight, 120) + 'px'
  }
}

function submit() {
  const trimmedText = text.value.trim()
  if (!trimmedText) return
  emit('send', trimmedText)
  text.value = ''
  if (textarea.value) textarea.value.style.height = 'auto'
}
</script>

<template>
  <div class="chat-input-container">
    <div class="chat-input-wrapper">
      <textarea
        ref="textarea"
        v-model="text"
        aria-label="Message to assistant"
        placeholder="Ask for recommendations, mark items as completed..."
        rows="1"
        :disabled="disabled"
        @keydown="onKeydown"
        @input="onInput"
      />
      <button class="btn btn-primary chat-send-btn" :disabled="disabled" @click="submit" aria-label="Send message">
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      </button>
    </div>
  </div>
</template>
