<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { RouterView } from 'vue-router'
import AppSidebar from '@/components/organisms/AppSidebar.vue'
import StatusBar from '@/components/organisms/StatusBar.vue'
import TokenGate from '@/components/organisms/TokenGate.vue'
import UpdateBanner from '@/components/organisms/UpdateBanner.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const app = useAppStore()
const auth = useAuthStore()
const theme = useThemeStore()

const sidebarOpen = ref(false)
const mainContent = ref<HTMLElement | null>(null)

function closeSidebar() {
  sidebarOpen.value = false
}

function load() {
  app.fetchStatus()
  app.fetchUsers()
  theme.fetchThemes()
}

onMounted(() => {
  // Themes are static files behind no token, and the gate is the one screen a
  // user who cannot read light-on-dark has no way to skip.
  theme.applyStoredTheme()
  // Every /api call 401s without a token, so nothing is fetched until the gate
  // has one, and the first accepted token is what starts the app.
  if (auth.isAuthenticated) load()
})

watch(
  () => auth.isAuthenticated,
  async (authenticated) => {
    if (!authenticated) return
    load()
    // The gate took the focused input with it, so focus would sit on <body>
    // and the next Tab would restart from the sidebar.
    await nextTick()
    mainContent.value?.focus()
  },
)
</script>

<template>
  <TokenGate v-if="!auth.isAuthenticated" />

  <template v-else>
    <!-- Mobile sidebar toggle -->
    <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen" aria-label="Toggle navigation">
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="3" y1="6" x2="21" y2="6" />
        <line x1="3" y1="12" x2="21" y2="12" />
        <line x1="3" y1="18" x2="21" y2="18" />
      </svg>
    </button>
    <div
      class="sidebar-overlay"
      :class="{ visible: sidebarOpen }"
      @click="closeSidebar"
    />

    <div class="app-layout" :class="{ 'sidebar-open': sidebarOpen }">
      <AppSidebar :user="app.currentUser" @navigate="closeSidebar" />
      <main id="main-content" ref="mainContent" class="main-content" tabindex="-1">
        <UpdateBanner />
        <StatusBar />
        <RouterView />
      </main>
    </div>
  </template>
</template>

<style>
/* Mobile sidebar state driven by Vue */
@media (max-width: 768px) {
  .sidebar-open .sidebar {
    left: 0;
  }
}
</style>
