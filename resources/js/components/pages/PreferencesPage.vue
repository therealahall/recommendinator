<script setup lang="ts">
import { onMounted } from 'vue'
import { usePreferencesStore } from '@/stores/preferences'
import ThemeSelector from '@/components/organisms/ThemeSelector.vue'
import ScoringPrefs from '@/components/organisms/ScoringPrefs.vue'
import RulesPrefs from '@/components/organisms/RulesPrefs.vue'
import ProfilePanel from '@/components/organisms/ProfilePanel.vue'

const prefs = usePreferencesStore()

onMounted(() => {
  prefs.load()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Preferences</h2>
      <p class="page-description">Customize how recommendations are generated.</p>
    </div>
    <div class="card" :aria-busy="prefs.loading || undefined">
      <div v-if="prefs.loading" class="empty-state">Loading preferences...</div>
      <template v-else>
        <ThemeSelector v-model="prefs.pendingTheme" />
        <ScoringPrefs />
        <RulesPrefs />
        <ProfilePanel />
        <div class="pref-actions">
          <button class="btn btn-primary" :disabled="prefs.saving" @click="prefs.save()">
            {{ prefs.saving ? 'Saving...' : 'Save Preferences' }}
          </button>
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
      </template>
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
  color: var(--color-success);
}

.text-error {
  color: var(--color-error);
}
</style>
