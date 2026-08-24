<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { usePreferencesStore } from '@/stores/preferences'
import { useThemeStore } from '@/stores/theme'
import ThemeSelector from '@/components/organisms/ThemeSelector.vue'
import ScoringPrefs from '@/components/organisms/ScoringPrefs.vue'
import RulesPrefs from '@/components/organisms/RulesPrefs.vue'
import ProfilePanel from '@/components/organisms/ProfilePanel.vue'

const prefs = usePreferencesStore()
const theme = useThemeStore()
const retrying = ref(false)
const retryMessage = ref('')
const page = ref<HTMLElement | null>(null)

onMounted(() => {
  prefs.load()
})

onBeforeRouteLeave(
  () =>
    !prefs.isDirty ||
    window.confirm('Your preference changes have not been saved. Discard them?'),
)

function resetToDefaults() {
  if (prefs.saving) return
  if (
    window.confirm(
      'Reset every preference to its defaults? This clears your scorer ' +
        'weights, custom rules, length preferences and theme.',
    )
  ) {
    prefs.resetToDefaults()
  }
}

async function onRetry(): Promise<void> {
  if (retrying.value) return
  const focused = document.activeElement
  retrying.value = true
  // The in-flight line is what makes a second failure audible: setting the same
  // words twice leaves the region's text unchanged, so it announces nothing.
  retryMessage.value = 'Reloading preferences…'
  await prefs.load()
  retrying.value = false
  retryMessage.value = prefs.loadError
    ? "Still couldn't load preferences. Try again in a moment."
    : 'Preferences loaded.'
  await nextTick()
  // Success unmounts the whole card, Retry included, dropping the keyboard user
  // to <body> (WCAG 2.4.3). Restored only when focus actually fell there:
  // someone who Tabbed away mid-request must not be yanked back.
  if (
    focused instanceof HTMLElement &&
    !focused.isConnected &&
    (document.activeElement === null || document.activeElement === document.body)
  ) {
    page.value?.focus()
  }
}
</script>

<template>
  <div
    ref="page"
    class="focus-fallback"
    :aria-busy="prefs.loading || undefined"
    role="group"
    aria-labelledby="preferences-heading"
    tabindex="-1"
  >
    <!-- aria-busy sits on this wrapper because it is the only node present in
         every outcome: assistive tech tracking the state has to hear the flag
         flip to false, and on the common path (preferences arrive) the card
         below is replaced by the form, so a flag there would vanish instead of
         clearing (4.1.3). Mirrors SettingsPage. -->
    <div class="page-header">
      <h2 id="preferences-heading">Preferences</h2>
      <p class="page-description">Customize how recommendations are generated.</p>
    </div>

    <!-- Outside the branches below, so it is already in the accessibility tree
         when a retry finally gives it something to say (WCAG 4.1.3). -->
    <p
      class="retry-status"
      data-testid="preferences-retry-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ retryMessage }}</p>

    <!-- The form renders only off server values. Save PUTs every field, so a
         form built on this store's empty defaults would blank what is stored. -->
    <div v-if="!prefs.hasLoaded" class="card">
      <!-- The failure branch outlives the retry it started: replacing it would
           unmount the button holding focus and drop the user to <body>
           (WCAG 2.4.3). -->
      <div v-if="prefs.loadError || retrying" class="empty-state">
        <!-- The Retry button sits OUTSIDE the alert: alert content is announced
             as one chunk, which buries the control's affordance. -->
        <span role="alert">Couldn't load preferences.</span>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="preferences-retry"
          :aria-disabled="retrying || undefined"
          @click="onRetry"
        >{{ retrying ? 'Retrying…' : 'Retry' }}</button>
      </div>
      <div v-else class="empty-state">Loading preferences...</div>
    </div>

    <div v-else class="card">
      <ThemeSelector
        :model-value="theme.currentThemeId ?? theme.defaultThemeId"
        @update:model-value="prefs.selectTheme"
      />
      <ScoringPrefs />
      <RulesPrefs />
      <ProfilePanel />
      <div class="pref-actions">
        <button class="btn btn-primary" :disabled="prefs.saving" @click="prefs.save()">
          {{ prefs.saving ? 'Saving...' : 'Save Preferences' }}
        </button>
        <button
          class="btn btn-secondary"
          :aria-disabled="prefs.saving || undefined"
          @click="resetToDefaults"
        >Reset to defaults</button>
        <!-- Outside the live region below: it changes on every keystroke, and
             announcing each one would talk over the field being typed in. -->
        <span
          v-if="prefs.isDirty"
          class="save-status"
          data-testid="preferences-dirty"
        >Unsaved changes</span>
        <div aria-live="polite" aria-atomic="true">
          <span
            v-if="prefs.saveStatus === 'saved'"
            class="save-status text-success"
          >Saved!</span>
          <span
            v-else-if="prefs.saveStatus === 'error'"
            class="save-status text-error"
          >Error: {{ prefs.saveError }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pref-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.save-status {
  font-size: var(--text-sm);
}

.text-success {
  color: var(--color-success-text);
}

.text-error {
  color: var(--color-error-text);
}
</style>
