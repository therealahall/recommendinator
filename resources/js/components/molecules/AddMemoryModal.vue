<script setup lang="ts">
import { ref } from 'vue'
import { useFocusTrap } from '@/composables/useFocusTrap'

const emit = defineEmits<{
  save: [text: string]
  close: []
}>()

const memoryText = ref('')
const modalContent = ref<HTMLElement | null>(null)
useFocusTrap(modalContent, () => emit('close'))

function save() {
  const trimmed = memoryText.value.trim()
  if (!trimmed) return
  emit('save', trimmed)
  memoryText.value = ''
}
</script>

<template>
  <div class="memory-modal" @click.self="emit('close')">
    <div ref="modalContent" class="memory-modal-content" role="dialog" aria-modal="true" aria-labelledby="add-memory-title" tabindex="-1">
      <h3 id="add-memory-title">Add Memory</h3>
      <label for="memory-textarea" class="sr-only">Memory text</label>
      <textarea id="memory-textarea" v-model="memoryText" placeholder="e.g., I prefer shorter games during weekdays" />
      <div class="memory-modal-actions">
        <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" @click="save">Save</button>
      </div>
    </div>
  </div>
</template>
