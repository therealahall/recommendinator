<script setup lang="ts">
import { onMounted, ref, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from '@/components/atoms/ChatMessage.vue'
import ChatInput from '@/components/atoms/ChatInput.vue'
import ChatWelcome from '@/components/molecules/ChatWelcome.vue'
import MemoryPanel from '@/components/organisms/MemoryPanel.vue'
import ProfilePanel from '@/components/organisms/ProfilePanel.vue'

const chat = useChatStore()
const messagesEl = ref<HTMLDivElement | null>(null)

onMounted(() => {
  chat.loadMemories()
  chat.loadProfile()
})

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

watch(() => chat.messages.length, scrollToBottom)

function onSend(text: string) {
  chat.send(text)
  scrollToBottom()
}

function onSuggest(text: string, contentType: string) {
  chat.send(text, contentType)
  scrollToBottom()
}

function onReset() {
  if (confirm('Reset the conversation? Memories will be preserved.')) {
    chat.reset()
  }
}
</script>

<template>
  <div class="chat-container">
    <div class="chat-main">
      <div ref="messagesEl" class="chat-messages">
        <ChatWelcome v-if="chat.showWelcome" @suggest="onSuggest" />
        <ChatMessage
          v-for="message in chat.messages"
          :key="message.id"
          :message="message"
        />
        <div v-if="chat.isStreaming" class="chat-message assistant typing">
          <div class="typing-dot" /><div class="typing-dot" /><div class="typing-dot" />
        </div>
      </div>
      <ChatInput :disabled="chat.isStreaming" @send="onSend" />
      <div class="chat-actions">
        <button class="btn btn-small btn-ghost" @click="onReset">Reset Chat</button>
      </div>
    </div>
    <div class="chat-sidebar">
      <MemoryPanel />
      <ProfilePanel />
    </div>
  </div>
</template>
