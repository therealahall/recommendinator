<script setup lang="ts">
import type { ChatMessage } from '@/types/chat'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{
  message: ChatMessage
}>()

const { renderMarkdown } = useMarkdown()
</script>

<template>
  <div v-if="message.isToolIndicator" class="tool-indicator" :class="{ success: message.toolSuccess }">
    <span class="tool-icon">{{ message.toolSuccess ? '✓' : '⚙' }}</span>
    {{ message.content }}
  </div>
  <div v-else class="chat-message" :class="message.role">
    <div
      v-if="message.role === 'assistant'"
      class="message-content"
      v-html="renderMarkdown(message.content)"
    />
    <div v-else class="message-content">{{ message.content }}</div>
  </div>
</template>
