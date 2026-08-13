<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { RouterView } from 'vue-router'
import AppSidebar from '@/components/organisms/AppSidebar.vue'
import LoginForm from '@/components/organisms/LoginForm.vue'
import SetupForm from '@/components/organisms/SetupForm.vue'
import StatusBar from '@/components/organisms/StatusBar.vue'
import UpdateBanner from '@/components/organisms/UpdateBanner.vue'
import { useSubmission } from '@/composables/useSubmission'
import { useAppStore } from '@/stores/app'
import { SESSION_ENDED, useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import type { LoginRequest, SetupRequest } from '@/types/api'

const app = useAppStore()
const auth = useAuthStore()
const theme = useThemeStore()
// Setup and sign-in are never on screen together, so they share one report.
const gate = useSubmission()

const sidebarOpen = ref(false)
const mainContent = ref<HTMLElement | null>(null)

function closeSidebar() {
  sidebarOpen.value = false
}

function load() {
  app.fetchStatus()
  theme.fetchThemes()
}

function signUp(account: SetupRequest) {
  gate.submit(() => auth.signUp(account))
}

function signIn(credentials: LoginRequest) {
  gate.submit(() => auth.signIn(credentials))
}

onMounted(() => {
  // Themes are static files behind no session, and the sign-in screen is the one
  // a user who cannot read light-on-dark has no way to skip.
  theme.applyStoredTheme()
  // One call decides all three screens, and every /api route 401s until it comes
  // back signed in, so nothing else is fetched before it answers.
  gate.submit(auth.resolveSession)
})

watch(
  () => auth.isAuthenticated,
  async (authenticated, wasAuthenticated) => {
    if (!authenticated) {
      // The shell disappearing without a word reads as a crash, and every route
      // out of it — a revoked session or a deliberate sign-out — ended one.
      if (wasAuthenticated) gate.error = SESSION_ENDED
      return
    }
    load()
    // The sign-in screen took the focused input with it, so focus would sit on
    // <body> and the next Tab would restart from the sidebar.
    await nextTick()
    mainContent.value?.focus()
  },
)
</script>

<template>
  <!-- Three screens off one answer, and a fourth state before it lands: while
       the session is unknown none of them render, because a guess flashes a
       sign-in form at someone who is already signed in. -->
  <SetupForm
    v-if="auth.needsSetup"
    :error="gate.error"
    :pending="gate.pending"
    @submit="signUp"
  />

  <LoginForm
    v-else-if="auth.needsLogin"
    :error="gate.error"
    :pending="gate.pending"
    @submit="signIn"
  />

  <template v-else-if="auth.isAuthenticated">
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
      <AppSidebar :user="auth.user" @navigate="closeSidebar" />
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
