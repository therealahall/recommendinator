<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
// Kept in step with the width base.css slides the sidebar off screen at.
const narrowViewport = window.matchMedia('(max-width: 768px)')
const isNarrow = ref(narrowViewport.matches)
/** `left: -100%` moves the closed sidebar out of sight and nothing else: every
 *  control in it stays tabbable, and stays in the accessibility tree. */
const sidebarOffscreen = computed(() => isNarrow.value && !sidebarOpen.value)
const mainContent = ref<HTMLElement | null>(null)
/** Why the sign-in form is on screen, as opposed to a refusal of what was typed
 *  into it: marking an untouched field invalid announces "invalid entry". */
const notice = ref('')

function closeSidebar() {
  sidebarOpen.value = false
}

function trackViewport(change: MediaQueryListEvent) {
  isNarrow.value = change.matches
}

function load() {
  app.fetchStatus()
  theme.fetchThemes()
}

/** Move what the gate is holding into the notice: it describes the screen the
 *  user has landed on rather than anything they typed into it. */
function noticeFromGate() {
  notice.value = gate.error
  gate.error = ''
}

async function signUp(account: SetupRequest) {
  await gate.submit(() => auth.signUp(account))
  // A lost race for the first account: the store has already moved the app off
  // the setup form, so "sign in instead" is advice about wherever it landed
  // rather than a refusal on the screen that is gone.
  if (!auth.needsSetup) noticeFromGate()
}

function signIn(credentials: LoginRequest) {
  // The user's own attempt supersedes whatever brought them to this form.
  notice.value = ''
  gate.submit(() => auth.signIn(credentials))
}

onMounted(() => narrowViewport.addEventListener('change', trackViewport))

onUnmounted(() => {
  narrowViewport.removeEventListener('change', trackViewport)
  app.stopPolling()
})

onMounted(async () => {
  // Themes are static files behind no session, and the sign-in screen is the one
  // a user who cannot read light-on-dark has no way to skip.
  theme.applyStoredTheme()
  // One call decides all three screens, and every /api route 401s until it comes
  // back signed in, so nothing else is fetched before it answers.
  await gate.submit(auth.resolveSession)
  // Whatever it has to say — an unreachable server, most of all — is about the
  // screen it chose, and there was no form to refuse anything on.
  noticeFromGate()
})

watch(
  () => auth.isAuthenticated,
  async (authenticated, wasAuthenticated) => {
    if (!authenticated) {
      // Every /api route 401s from here, and each refusal signs the session out
      // again, so a poll left running talks to the sign-in screen forever.
      app.stopPolling()
      // The shell disappearing without a word reads as a crash, and every route
      // out of it — a revoked session or a deliberate sign-out — ended one. Said
      // one tick late, because a live region that first enters the tree already
      // populated is read as page content and skipped.
      if (!wasAuthenticated) return
      notice.value = ''
      await nextTick()
      notice.value = SESSION_ENDED
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
  <!-- Three screens off one answer, and a fourth screen before it lands:
       guessing which of the three would flash a sign-in form at someone already
       signed in, but rendering nothing leaves a phone on a slow connection with
       an empty document and no way to tell it apart from a broken one. -->
  <SetupForm
    v-if="auth.needsSetup"
    :error="gate.error"
    :pending="gate.pending"
    :min-password-length="auth.minPasswordLength"
    @submit="signUp"
  />

  <LoginForm
    v-else-if="auth.needsLogin"
    :error="gate.error"
    :notice="notice"
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
      <AppSidebar :user="auth.user" :offscreen="sidebarOffscreen" @navigate="closeSidebar" />
      <main id="main-content" ref="mainContent" class="main-content" tabindex="-1">
        <UpdateBanner />
        <StatusBar />
        <RouterView />
      </main>
    </div>
  </template>

  <main v-else class="auth-screen" aria-busy="true">
    <!-- No live region: this is the document a screen reader arrives at, and one
         that enters the tree already populated is skipped as page content. -->
    <div class="card auth-card">
      <header class="auth-head">
        <h1 class="auth-title">Recommendinator</h1>
      </header>
      <p class="auth-status" data-testid="session-pending">Checking your session…</p>
    </div>
  </main>
</template>

<style>
/* Mobile sidebar state driven by Vue */
@media (max-width: 768px) {
  .sidebar-open .sidebar {
    left: 0;
  }
}
</style>
