<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { RouterView } from 'vue-router'
import router from '@/router'
import AppNav from '@/components/organisms/AppNav.vue'
import LoginForm from '@/components/organisms/LoginForm.vue'
import SetupForm from '@/components/organisms/SetupForm.vue'
import StatusBar from '@/components/organisms/StatusBar.vue'
import UpdateBanner from '@/components/organisms/UpdateBanner.vue'
import WeightsPanel from '@/components/organisms/WeightsPanel.vue'
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

const mainContent = ref<HTMLElement | null>(null)
/** Why the sign-in form is on screen, as opposed to a refusal of what was typed
 *  into it: marking an untouched field invalid announces "invalid entry". */
const notice = ref('')

// The weights only change what a ranking run produces, so they are summonable
// from the screen that shows one and nowhere else.
const ranking = computed(() => router.currentRoute.value.name === 'recommendations')

// Prevented rather than followed: the router owns the fragment, so navigating to
// #main-content would throw the route away.
function skipToMain() {
  mainContent.value?.focus()
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

onUnmounted(() => {
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

// Setup, sign-in and the session check render outside RouterView, so the route
// behind them titles a page that is not on screen (WCAG 2.4.2).
watchEffect(() => {
  const page = auth.isAuthenticated ? router.currentRoute.value.meta.title : ''
  document.title = page ? `${page} · Recommendinator` : 'Recommendinator'
})

watch(
  () => auth.isAuthenticated,
  async (authenticated, wasAuthenticated) => {
    if (!authenticated) {
      // Every /api route 401s from here, and each refusal signs the session out
      // again, so a poll left running talks to the sign-in screen forever.
      app.stopPolling()
      // The shell disappearing without a word reads as a crash, and every route
      // out of it — a revoked session or a deliberate sign-out — ended one.
      if (!wasAuthenticated) return
      // Said one tick late, because a live region that first enters the tree already
      // populated is read as page content and skipped.
      notice.value = ''
      await nextTick()
      notice.value = SESSION_ENDED
      return
    }
    load()
    // The sign-in screen took the focused input with it, so focus would sit on
    // <body> and the next Tab would restart from the nav.
    await nextTick()
    mainContent.value?.focus()
  },
)
</script>

<template>
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
    <a class="skip-link" href="#main-content" @click.prevent="skipToMain">Skip to main content</a>

    <div class="app-shell">
      <AppNav :user="auth.user" />
      <div class="app-column">
        <!-- The h1 lives here rather than in the nav: the nav is a row of tabs
             on a phone, with no room for a name the page still owes a heading. -->
        <header class="app-topbar">
          <h1 class="app-name">Recommendinator</h1>
          <span v-if="app.version" class="badge">v{{ app.version }}</span>
          <div v-if="ranking" class="app-topbar-actions">
            <WeightsPanel />
          </div>
        </header>
        <main id="main-content" ref="mainContent" class="app-stage" tabindex="-1">
          <div class="app-stage-inner">
            <UpdateBanner />
            <StatusBar />
            <RouterView />
          </div>
        </main>
      </div>
    </div>
  </template>

  <!-- Rendering nothing leaves a phone on a slow connection with an empty
       document and no way to tell it apart from a broken one. -->
  <main v-else class="auth-screen" aria-busy="true">
    <!-- No live region: this is the document a screen reader arrives at, and one
         that enters the tree already populated is skipped as page content. -->
    <div class="card auth-card">
      <header class="auth-head">
        <p class="auth-eyebrow">Recommendinator</p>
        <h1 class="auth-title">Just a moment</h1>
      </header>
      <p class="state state--loading" data-testid="session-pending">
        <span class="spinner" aria-hidden="true" /> Checking your session…
      </p>
    </div>
  </main>
</template>
