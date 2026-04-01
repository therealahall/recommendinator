<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const route = useRoute()
const app = useAppStore()

const emit = defineEmits<{
  navigate: []
}>()

function navigate(name: string) {
  router.push({ name })
  emit('navigate')
}

function isActive(name: string): boolean {
  return route.name === name
}

function onUserChange(event: Event) {
  const select = event.target as HTMLSelectElement
  app.setUser(parseInt(select.value, 10))
}
</script>

<template>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <h1>Recommendinator</h1>
      <span v-if="app.version" class="version-label">v{{ app.version }}</span>
    </div>
    <nav class="sidebar-nav">
      <!-- Recommendations -->
      <button class="nav-item" :class="{ active: isActive('recommendations') }" :aria-current="isActive('recommendations') ? 'page' : undefined" @click="navigate('recommendations')">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
        </svg>
        Recommendations
      </button>
      <!-- Library -->
      <button class="nav-item" :class="{ active: isActive('library') }" :aria-current="isActive('library') ? 'page' : undefined" @click="navigate('library')">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
        Library
      </button>
      <!-- Chat (AI only) -->
      <button v-if="app.chatEnabled" class="nav-item" :class="{ active: isActive('chat') }" :aria-current="isActive('chat') ? 'page' : undefined" @click="navigate('chat')">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        Chat
      </button>
      <!-- Data -->
      <button class="nav-item" :class="{ active: isActive('data') }" :aria-current="isActive('data') ? 'page' : undefined" @click="navigate('data')">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
        Data
      </button>
      <!-- Preferences -->
      <button class="nav-item" :class="{ active: isActive('preferences') }" :aria-current="isActive('preferences') ? 'page' : undefined" @click="navigate('preferences')">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
        Preferences
      </button>
    </nav>
    <div class="sidebar-footer">
      <label for="userSelect">User</label>
      <select id="userSelect" :value="app.currentUserId" @change="onUserChange">
        <option v-for="user in app.users" :key="user.id" :value="user.id">
          {{ user.display_name || user.username }}
        </option>
      </select>
    </div>
  </aside>
</template>
