<script setup lang="ts">
import { SUGGESTIONS } from '@/constants/chat'

const emit = defineEmits<{
  suggest: [text: string, contentType: string]
}>()
</script>

<template>
  <div class="chat-welcome">
    <h3>Chat with the Recommendinator</h3>
    <p>Ask for recommendations, mark items as completed, or tell me about your preferences.</p>
    <div class="chat-suggestions">
      <button
        v-for="s in SUGGESTIONS"
        :key="s.contentType"
        class="suggestion-btn"
        @click="emit('suggest', s.text, s.contentType)"
      >{{ s.label }}</button>
    </div>
  </div>
</template>
