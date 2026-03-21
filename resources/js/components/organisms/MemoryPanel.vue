<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import AddMemoryModal from '@/components/molecules/AddMemoryModal.vue'

const chat = useChatStore()
const showAddModal = ref(false)

function onSaveMemory(text: string) {
  chat.addMemory(text)
  showAddModal.value = false
}
</script>

<template>
  <div class="memory-panel">
    <h4>Memories</h4>
    <p class="memory-help">Your preferences and statements I remember.</p>
    <div class="memory-list">
      <div v-if="chat.memories.length === 0" class="empty-state">No memories yet</div>
      <div
        v-for="m in chat.memories"
        :key="m.id"
        class="memory-item"
        :class="[m.memory_type === 'user_stated' ? 'user-stated' : 'inferred', { inactive: !m.is_active }]"
      >
        <div class="memory-text">{{ m.memory_text }}</div>
        <div class="memory-meta">
          <span class="memory-type">{{ m.memory_type === 'user_stated' ? 'Stated' : 'Inferred' }}</span>
          <div class="memory-actions">
            <button @click="chat.toggleMemory(m.id, m.is_active)">
              {{ m.is_active ? 'Disable' : 'Enable' }}
            </button>
            <button class="delete" @click="chat.deleteMemory(m.id)">Delete</button>
          </div>
        </div>
      </div>
    </div>
    <button class="btn btn-small btn-secondary mt-2" @click="showAddModal = true">Add Memory</button>
    <AddMemoryModal
      v-if="showAddModal"
      @save="onSaveMemory"
      @close="showAddModal = false"
    />
  </div>
</template>
