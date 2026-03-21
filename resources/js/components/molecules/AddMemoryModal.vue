<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  save: [text: string]
  close: []
}>()

const memoryText = ref('')

function save() {
  const trimmed = memoryText.value.trim()
  if (!trimmed) return
  emit('save', trimmed)
  memoryText.value = ''
}
</script>

<template>
  <div class="memory-modal" @click.self="emit('close')">
    <div class="memory-modal-content">
      <h3>Add Memory</h3>
      <textarea v-model="memoryText" placeholder="e.g., I prefer shorter games during weekdays" />
      <div class="memory-modal-actions">
        <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" @click="save">Save</button>
      </div>
    </div>
  </div>
</template>
