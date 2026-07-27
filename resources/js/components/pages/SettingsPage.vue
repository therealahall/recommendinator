<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import SettingsSection from '@/components/organisms/SettingsSection.vue'

const store = useSettingsStore()

onMounted(() => {
  store.load()
})
</script>

<template>
  <div :aria-busy="store.loading || undefined">
    <!-- aria-busy sits on this wrapper because it is the only node present in
         every outcome: assistive tech tracking the state has to hear the flag
         flip to false, and on the common path (settings arrive) the card below
         is replaced by the section list, so a flag there would vanish instead
         of clearing (4.1.3). Mirrors PreferencesPage. -->
    <div class="page-header">
      <h2>Settings</h2>
      <p class="page-description">Application configuration for this instance.</p>
    </div>

    <div
      v-if="store.loading || store.loadError || store.sections.length === 0"
      class="card"
    >
      <div v-if="store.loading" class="empty-state">Loading settings…</div>

      <div v-else-if="store.loadError" class="empty-state">
        <!-- The Retry button sits OUTSIDE the alert: alert content is announced
             as one chunk, which buries the control's affordance. -->
        <span role="alert">Couldn't load settings.</span>
        <button
          type="button"
          class="btn btn-secondary"
          data-testid="settings-retry"
          @click="store.load()"
        >Retry</button>
      </div>

      <div v-else class="empty-state">No configurable settings.</div>
    </div>

    <template v-else>
      <SettingsSection
        v-for="section in store.sections"
        :key="section.section"
        :section="section"
      />
    </template>
  </div>
</template>
